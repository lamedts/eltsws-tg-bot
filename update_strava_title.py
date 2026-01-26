#!/usr/bin/env python3
"""🏃 Strava Activity Title Updater
===================================

This script updates the title of your latest Strava activity.

Setup:
1. Create a Strava API application at https://www.strava.com/settings/api
2. Set environment variables or enter credentials when prompted:
   - STRAVA_CLIENT_ID
   - STRAVA_CLIENT_SECRET
3. On first run, you'll be prompted to authorize the app in your browser

Usage:
    python update_strava_title.py "My New Activity Title"
    python update_strava_title.py  # Will prompt for new title
"""

import os
import sys
import json
import webbrowser
import http.server
import socketserver
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import requests

# Token storage file
TOKEN_FILE = Path(__file__).parent / ".strava_tokens.json"

# Strava API endpoints
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


def get_credentials():
    """Get Strava API credentials from environment or user input."""
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")

    if not client_id:
        client_id = input("Enter your Strava Client ID: ").strip()
    if not client_secret:
        client_secret = input("Enter your Strava Client Secret: ").strip()

    return client_id, client_secret


def save_tokens(tokens: dict):
    """Save tokens to file."""
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    print(f"✅ Tokens saved to {TOKEN_FILE}")


def load_tokens() -> dict | None:
    """Load tokens from file if they exist."""
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Refresh the access token using the refresh token."""
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


def get_authorization_code(client_id: str, redirect_port: int = 8000) -> str:
    """Open browser for OAuth authorization and capture the code."""
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
                    <html>
                    <body style="font-family: sans-serif; text-align: center; padding: 50px;">
                        <h1>&#10004; Authorization Successful!</h1>
                        <p>You can close this window and return to the terminal.</p>
                    </body>
                    </html>
                """)
            else:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Authorization failed</h1></body></html>")

        def log_message(self, format, *args):
            pass  # Suppress HTTP logging

    print(f"\n🌐 Opening browser for Strava authorization...")
    print(f"   If it doesn't open automatically, visit:\n   {auth_url}\n")
    webbrowser.open(auth_url)

    with socketserver.TCPServer(("", redirect_port), OAuthHandler) as httpd:
        httpd.handle_request()

    if not authorization_code:
        raise Exception("Failed to get authorization code")

    return authorization_code


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange authorization code for access and refresh tokens."""
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


def get_valid_access_token(client_id: str, client_secret: str) -> str:
    """Get a valid access token, refreshing or re-authenticating if needed."""
    import time

    tokens = load_tokens()

    if tokens:
        # Check if token is expired
        if tokens.get("expires_at", 0) > time.time():
            print("✅ Using existing access token")
            return tokens["access_token"]

        # Try to refresh
        print("🔄 Refreshing access token...")
        try:
            new_tokens = refresh_access_token(
                client_id, client_secret, tokens["refresh_token"]
            )
            save_tokens(new_tokens)
            return new_tokens["access_token"]
        except Exception as e:
            print(f"⚠️  Token refresh failed: {e}")
            print("   Re-authenticating...")

    # Need to do full OAuth flow
    code = get_authorization_code(client_id)
    tokens = exchange_code_for_tokens(client_id, client_secret, code)
    save_tokens(tokens)
    return tokens["access_token"]


def get_latest_activity(access_token: str) -> dict:
    """Get the most recent activity."""
    response = requests.get(
        f"{STRAVA_API_BASE}/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": 1, "page": 1},
    )
    response.raise_for_status()
    activities = response.json()

    if not activities:
        raise Exception("No activities found on your Strava account")

    return activities[0]


def update_activity_title(access_token: str, activity_id: int, new_title: str) -> dict:
    """Update the title (name) of an activity."""
    response = requests.put(
        f"{STRAVA_API_BASE}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"name": new_title},
    )
    response.raise_for_status()
    return response.json()


def main():
    print("=" * 50)
    print("🏃 Strava Activity Title Updater")
    print("=" * 50)

    # Get credentials
    client_id, client_secret = get_credentials()

    # Get access token
    access_token = get_valid_access_token(client_id, client_secret)

    # Get latest activity
    print("\n📥 Fetching latest activity...")
    activity = get_latest_activity(access_token)

    print(f"\n📋 Latest Activity:")
    print(f"   ID:       {activity['id']}")
    print(f"   Name:     {activity['name']}")
    print(f"   Type:     {activity['type']}")
    print(f"   Date:     {activity['start_date_local']}")
    if activity.get("distance"):
        print(f"   Distance: {activity['distance']/1000:.2f} km")

    # Get new title
    if len(sys.argv) > 1:
        new_title = " ".join(sys.argv[1:])
    else:
        print()
        new_title = input("Enter new title (or press Enter to cancel): ").strip()
        if not new_title:
            print("❌ Cancelled - no changes made")
            return

    # Update title
    print(f"\n🔄 Updating title to: '{new_title}'...")
    updated = update_activity_title(access_token, activity["id"], new_title)

    print(f"\n✅ Success! Activity title updated:")
    print(f"   Old: {activity['name']}")
    print(f"   New: {updated['name']}")
    print(f"\n🔗 View on Strava: https://www.strava.com/activities/{activity['id']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Cancelled by user")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ Strava API error: {e}")
        if hasattr(e, "response") and e.response is not None:
            try:
                print(f"   Details: {e.response.json()}")
            except Exception:
                pass
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)
