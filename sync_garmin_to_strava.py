#!/usr/bin/env python3
from __future__ import annotations
"""🔄 Garmin to Strava Activity Title Sync
==========================================

This script reads the latest activity from Garmin Connect and updates
the matching activity's title on Strava.

Matching Logic:
- Matches activities by start time (within 5 minutes tolerance)
- Optionally validates by distance (within 10% tolerance)

Setup:
1. Garmin: Set EMAIL and PASSWORD environment variables, or enter when prompted
2. Strava: Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET, or enter when prompted
   - Create a Strava API app at https://www.strava.com/settings/api

Usage:
    python sync_garmin_to_strava.py
"""

import json
import os
import sys
import time
import webbrowser
import http.server
import socketserver
from datetime import datetime
from getpass import getpass
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv
import requests
from garth.exc import GarthHTTPError
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

# Load environment variables from .env file
load_dotenv()

# ===== Configuration =====
GARMIN_TOKEN_DIR = Path(__file__).parent / ".garminconnect"
STRAVA_TOKEN_FILE = Path(__file__).parent / ".strava_tokens.json"

# Matching tolerances
TIME_TOLERANCE_SECONDS = 300  # 5 minutes
DISTANCE_TOLERANCE_PERCENT = 0.1  # 10%

# Strava API endpoints
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


# ===== Garmin Functions =====

def garmin_api_call(fn, *args, retries=3, **kwargs):
    """Call a Garmin API method with retry and exponential backoff on rate limits."""
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except GarminConnectTooManyRequestsError:
            if attempt < retries - 1:
                wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
                print(f"⏳ Garmin rate limit hit, waiting {wait}s before retry ({attempt + 1}/{retries})...")
                time.sleep(wait)
            else:
                raise
        except GarthHTTPError as e:
            error_str = str(e)
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 429 or "429" in error_str:
                if attempt < retries - 1:
                    wait = 30 * (2 ** attempt)
                    print(f"⏳ Garmin rate limit hit, waiting {wait}s before retry ({attempt + 1}/{retries})...")
                    time.sleep(wait)
                else:
                    raise
            elif status in (500, 503) or "500" in error_str or "503" in error_str:
                if attempt < retries - 1:
                    wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                    print(f"⏳ Garmin server error, waiting {wait}s before retry ({attempt + 1}/{retries})...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise


def get_garmin_credentials():
    """Get Garmin credentials from environment or user input."""
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")

    if not email:
        email = input("Garmin login email: ")
    if not password:
        password = getpass("Garmin password: ")

    return email, password


def _login_with_retry(login_fn, *args, retries=3, **kwargs):
    """Retry a Garmin login call with exponential backoff on rate limits."""
    for attempt in range(retries):
        try:
            return login_fn(*args, **kwargs)
        except GarminConnectTooManyRequestsError:
            if attempt < retries - 1:
                wait = 60 * (2 ** attempt)  # 60s, 120s, 240s
                print(f"⏳ Garmin login rate limit hit, waiting {wait}s before retry ({attempt + 1}/{retries})...")
                time.sleep(wait)
            else:
                raise
        except GarthHTTPError as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 429 or "429" in str(e):
                if attempt < retries - 1:
                    wait = 60 * (2 ** attempt)
                    print(f"⏳ Garmin login rate limit hit, waiting {wait}s before retry ({attempt + 1}/{retries})...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise


def init_garmin_api() -> Garmin | None:
    """Initialize Garmin API with authentication and token management."""
    tokenstore_path = GARMIN_TOKEN_DIR

    # Try to login with stored tokens first
    try:
        garmin = Garmin()
        _login_with_retry(garmin.login, str(tokenstore_path))
        print("✅ Garmin: Using existing tokens")
        return garmin
    except (FileNotFoundError, GarthHTTPError, GarminConnectAuthenticationError, GarminConnectConnectionError):
        pass

    # Need to authenticate
    print("🔐 Garmin: Authentication required")
    while True:
        try:
            email, password = get_garmin_credentials()
            garmin = Garmin(email=email, password=password, is_cn=False, return_on_mfa=True)
            result1, result2 = _login_with_retry(garmin.login)

            if result1 == "needs_mfa":
                mfa_code = input("Enter MFA code: ")
                garmin.resume_login(result2, mfa_code)

            # Save tokens
            garmin.garth.dump(str(tokenstore_path))
            print("✅ Garmin: Authenticated and tokens saved")
            return garmin

        except GarminConnectAuthenticationError:
            print("❌ Invalid credentials, please try again")
            continue
        except Exception as e:
            print(f"❌ Garmin auth error: {e}")
            return None


def get_garmin_latest_activity(garmin: Garmin) -> dict | None:
    """Get the latest activity from Garmin Connect."""
    try:
        activities = garmin_api_call(garmin.get_activities, 0, 1)
        if activities:
            return activities[0]
        return None
    except Exception as e:
        print(f"❌ Error fetching Garmin activities: {e}")
        return None


def get_garmin_activities_since(garmin: Garmin, hours: int = 24) -> list:
    """Get all Garmin activities from the past N hours."""
    from datetime import datetime, timedelta

    try:
        # Get more activities to ensure we cover the time range
        activities = garmin_api_call(garmin.get_activities, 0, 20)
        if not activities:
            return []

        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_activities = []

        for activity in activities:
            try:
                time_str = activity.get("startTimeLocal", "")
                activity_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                if activity_time >= cutoff_time:
                    recent_activities.append(activity)
            except (ValueError, TypeError):
                continue

        return recent_activities
    except Exception as e:
        print(f"❌ Error fetching Garmin activities: {e}")
        return []


def get_garmin_activities(garmin: Garmin, count: int = 10) -> list:
    """Get the last N activities from Garmin Connect."""
    try:
        activities = garmin_api_call(garmin.get_activities, 0, count)
        return activities if activities else []
    except Exception as e:
        print(f"❌ Error fetching Garmin activities: {e}")
        return []


# ===== Strava Functions =====

def get_strava_credentials(interactive: bool = True):
    """Get Strava API credentials from environment, tokens file, or user input.

    Args:
        interactive: If True, prompt for missing credentials. If False, raise error.
    """
    # First check environment variables
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")

    # If not in env, try loading from tokens file
    if not client_id or not client_secret:
        tokens = load_strava_tokens()
        if tokens:
            client_id = client_id or tokens.get("client_id")
            client_secret = client_secret or tokens.get("client_secret")

    # If still missing
    if not client_id or not client_secret:
        if not interactive:
            raise ValueError(
                "Missing Strava credentials. Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET "
                "in .env file or run sync_garmin_to_strava.py first to save credentials."
            )
        # Ask user in interactive mode
        if not client_id:
            client_id = input("Strava Client ID: ").strip()
        if not client_secret:
            client_secret = input("Strava Client Secret: ").strip()

    return client_id, client_secret


def save_strava_tokens(tokens: dict, client_id: str = None, client_secret: str = None):
    """Save Strava tokens to file, including credentials."""
    # Preserve existing credentials if not provided
    if client_id:
        tokens["client_id"] = client_id
    if client_secret:
        tokens["client_secret"] = client_secret

    with open(STRAVA_TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def load_strava_tokens() -> dict | None:
    """Load Strava tokens from file."""
    if STRAVA_TOKEN_FILE.exists():
        with open(STRAVA_TOKEN_FILE, "r") as f:
            return json.load(f)
    return None


def refresh_strava_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Refresh the Strava access token."""
    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    response.raise_for_status()
    return response.json()


def get_strava_authorization_code(client_id: str, redirect_port: int = 8000) -> str:
    """Open browser for Strava OAuth and capture the code."""
    redirect_uri = f"http://localhost:{redirect_port}"
    auth_url = (
        f"{STRAVA_AUTH_URL}?"
        f"client_id={client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code&"
        f"approval_prompt=force&"
        f"scope=activity:read_all,activity:write"
    )

    authorization_code = None

    class OAuthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            nonlocal authorization_code
            query = urlparse(self.path).query
            params = parse_qs(query)

            if "code" in params:
                authorization_code = params["code"][0]
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"""
                    <html><body style="font-family: sans-serif; text-align: center; padding: 50px;">
                    <h1>&#10004; Strava Authorization Successful!</h1>
                    <p>You can close this window.</p>
                    </body></html>
                """)
            else:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authorization failed</h1></body></html>")

        def log_message(self, format, *args):
            pass

    print(f"\n🌐 Opening browser for Strava authorization...")
    webbrowser.open(auth_url)

    with socketserver.TCPServer(("", redirect_port), OAuthHandler) as httpd:
        httpd.handle_request()

    if not authorization_code:
        raise Exception("Failed to get Strava authorization code")

    return authorization_code


def exchange_strava_code(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange authorization code for tokens."""
    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    return response.json()


def get_strava_access_token(client_id: str, client_secret: str) -> str:
    """Get a valid Strava access token."""
    tokens = load_strava_tokens()

    if tokens:
        if tokens.get("expires_at", 0) > time.time():
            print("✅ Strava: Using existing access token")
            return tokens["access_token"]

        print("🔄 Strava: Refreshing access token...")
        try:
            new_tokens = refresh_strava_token(client_id, client_secret, tokens["refresh_token"])
            save_strava_tokens(new_tokens)
            return new_tokens["access_token"]
        except Exception as e:
            print(f"⚠️  Token refresh failed: {e}, re-authenticating...")

    # Full OAuth flow
    code = get_strava_authorization_code(client_id)
    tokens = exchange_strava_code(client_id, client_secret, code)
    save_strava_tokens(tokens)
    print("✅ Strava: Authenticated and tokens saved")
    return tokens["access_token"]


def strava_api_call(method: str, url: str, retries: int = 3, **kwargs) -> requests.Response:
    """Make a Strava API request with retry and backoff on rate limits (429)."""
    for attempt in range(retries):
        response = requests.request(method, url, **kwargs)
        if response.status_code == 429:
            if attempt < retries - 1:
                wait = 30 * (2 ** attempt)  # 30s, 60s, 120s
                print(f"⏳ Strava rate limit hit, waiting {wait}s before retry ({attempt + 1}/{retries})...")
                time.sleep(wait)
                continue
        response.raise_for_status()
        return response
    # Should not reach here, but just in case
    response.raise_for_status()
    return response


def get_strava_activities(access_token: str, count: int = 5) -> list:
    """Get recent Strava activities."""
    response = strava_api_call(
        "GET",
        f"{STRAVA_API_BASE}/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": count, "page": 1},
    )
    return response.json()


def update_strava_activity(access_token: str, activity_id: int, new_name: str) -> dict:
    """Update a Strava activity's name."""
    response = strava_api_call(
        "PUT",
        f"{STRAVA_API_BASE}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": new_name},
    )
    return response.json()


# ===== Matching Logic =====

def parse_garmin_time(time_str: str) -> datetime:
    """Parse Garmin activity time string to datetime."""
    # Garmin uses format like "2026-01-24 07:01:58" for startTimeLocal
    try:
        return datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        # Try ISO format
        return datetime.fromisoformat(time_str.replace("Z", "+00:00").replace("+00:00", ""))


def parse_strava_time(time_str: str) -> datetime:
    """Parse Strava activity time string to datetime."""
    # Strava uses ISO format like "2026-01-24T07:01:58Z"
    return datetime.fromisoformat(time_str.replace("Z", "+00:00").replace("+00:00", ""))


def find_matching_strava_activity(garmin_activity: dict, strava_activities: list) -> dict | None:
    """Find a Strava activity that matches the Garmin activity."""
    garmin_time = parse_garmin_time(garmin_activity.get("startTimeLocal", ""))
    garmin_distance = garmin_activity.get("distance", 0)  # meters

    best_match = None
    best_time_diff = float("inf")

    for strava_act in strava_activities:
        strava_time = parse_strava_time(strava_act.get("start_date_local", ""))
        time_diff = abs((garmin_time - strava_time).total_seconds())

        # Check time tolerance
        if time_diff <= TIME_TOLERANCE_SECONDS:
            # If distances available, verify they match
            strava_distance = strava_act.get("distance", 0)

            if garmin_distance > 0 and strava_distance > 0:
                distance_diff = abs(garmin_distance - strava_distance) / garmin_distance
                if distance_diff > DISTANCE_TOLERANCE_PERCENT:
                    continue  # Distance mismatch, skip

            # This is a potential match
            if time_diff < best_time_diff:
                best_time_diff = time_diff
                best_match = strava_act

    return best_match


# ===== Main =====

def main():
    print("=" * 55)
    print("🔄 Garmin to Strava Activity Title Sync")
    print("=" * 55)

    # Initialize Garmin
    print("\n📱 Connecting to Garmin Connect...")
    garmin = init_garmin_api()
    if not garmin:
        print("❌ Failed to connect to Garmin. Exiting.")
        return

    # Get latest Garmin activity
    print("\n📥 Fetching latest Garmin activity...")
    garmin_activity = get_garmin_latest_activity(garmin)
    if not garmin_activity:
        print("❌ No Garmin activities found. Exiting.")
        return

    garmin_name = garmin_activity.get("activityName", "Unknown")
    garmin_time = garmin_activity.get("startTimeLocal", "Unknown")
    garmin_distance = garmin_activity.get("distance", 0) / 1000  # Convert to km

    print(f"\n📋 Latest Garmin Activity:")
    print(f"   Name:     {garmin_name}")
    print(f"   Time:     {garmin_time}")
    print(f"   Distance: {garmin_distance:.2f} km")

    # Initialize Strava
    print("\n🏃 Connecting to Strava...")
    client_id, client_secret = get_strava_credentials()
    access_token = get_strava_access_token(client_id, client_secret)

    # Get recent Strava activities
    print("\n📥 Fetching recent Strava activities...")
    strava_activities = get_strava_activities(access_token, count=10)
    if not strava_activities:
        print("❌ No Strava activities found. Exiting.")
        return

    # Find matching activity
    print("\n🔍 Searching for matching Strava activity...")
    matching_strava = find_matching_strava_activity(garmin_activity, strava_activities)

    if not matching_strava:
        print("❌ No matching Strava activity found.")
        print("\n   Recent Strava activities:")
        for act in strava_activities[:5]:
            print(f"   - {act.get('name')} ({act.get('start_date_local')})")
        return

    strava_name = matching_strava.get("name", "Unknown")
    strava_id = matching_strava.get("id")
    strava_time = matching_strava.get("start_date_local", "")
    strava_distance = matching_strava.get("distance", 0) / 1000

    print(f"\n✅ Found matching Strava activity:")
    print(f"   Current Name: {strava_name}")
    print(f"   Time:         {strava_time}")
    print(f"   Distance:     {strava_distance:.2f} km")

    # Check if names already match
    if strava_name == garmin_name:
        print(f"\n✅ Titles already match! No update needed.")
        return

    # Confirm and update
    print(f"\n📝 Proposed title change:")
    print(f"   From: {strava_name}")
    print(f"   To:   {garmin_name}")

    confirm = input("\nProceed with update? (y/n): ").strip().lower()
    if confirm != "y":
        print("❌ Update cancelled.")
        return

    # Update Strava activity
    print("\n🔄 Updating Strava activity title...")
    updated = update_strava_activity(access_token, strava_id, garmin_name)

    print(f"\n✅ Success! Activity title updated:")
    print(f"   Old: {strava_name}")
    print(f"   New: {updated.get('name')}")
    print(f"\n🔗 View on Strava: https://www.strava.com/activities/{strava_id}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ API error: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                print(f"   Details: {e.response.json()}")
            except Exception:
                pass
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
