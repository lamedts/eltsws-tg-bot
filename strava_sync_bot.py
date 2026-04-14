#!/usr/bin/env python3
"""🤖 Telegram Bot for Garmin to Strava Title Sync
=================================================

This bot syncs activity titles from Garmin Connect to Strava via Telegram commands.

Commands:
    /start - Welcome message and usage info
    /sync  - Sync the latest Garmin activity title to matching Strava activity

Setup:
    1. Create a bot via @BotFather on Telegram
    2. Set TELEGRAM_BOT_TOKEN environment variable
    3. Ensure Garmin and Strava credentials are configured (see sync_garmin_to_strava.py)

Usage:
    python strava_sync_bot.py
"""

import logging
import os
import sys
import time

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Load environment variables from .env file
load_dotenv()

# Import sync functions from existing script
from sync_garmin_to_strava import (
    init_garmin_api,
    get_garmin_activities_since,
    get_garmin_activities,
    get_strava_credentials,
    get_strava_access_token,
    get_strava_activities,
    find_matching_strava_activity,
    update_strava_activity,
)

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== Global State =====
_cached_garmin = None  # Reuse Garmin session across commands
_last_sync_time = 0  # Timestamp of last sync for debounce
SYNC_COOLDOWN_SECONDS = 60  # Minimum seconds between syncs
ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))


def _is_allowed(update: Update) -> bool:
    """Check if the message is from the allowed chat ID."""
    chat_id = update.effective_chat.id
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        logger.warning(f"Ignored message from unauthorized chat_id={chat_id}")
        return False
    return True


def _get_garmin_session():
    """Get or create a cached Garmin session. Re-initializes on failure."""
    global _cached_garmin
    if _cached_garmin is not None:
        # Validate the session still works with a lightweight call
        try:
            _cached_garmin.get_activities(0, 1)
            return _cached_garmin
        except Exception:
            logger.info("Cached Garmin session expired, re-initializing...")
            _cached_garmin = None
    _cached_garmin = init_garmin_api()
    return _cached_garmin


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "🔄 *Garmin to Strava Sync Bot*\n\n"
        "Commands:\n"
        "/sync - Sync activities from past 24 hours\n"
        "/sync\\_last\\_10 - Sync the last 10 activities\n\n"
        "This bot syncs activity titles from Garmin to Strava.",
        parse_mode="Markdown"
    )


async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync command - sync all Garmin activities from past 24 hours to Strava."""
    if not _is_allowed(update):
        return
    global _last_sync_time

    # Debounce: reject if last sync was too recent
    elapsed = time.time() - _last_sync_time
    if elapsed < SYNC_COOLDOWN_SECONDS:
        remaining = int(SYNC_COOLDOWN_SECONDS - elapsed)
        await update.message.reply_text(f"⏳ Please wait {remaining}s before syncing again.")
        return

    await update.message.reply_text("🔄 Syncing activities from past 24 hours...")
    _last_sync_time = time.time()

    try:
        # Step 1: Connect to Garmin (uses cached session)
        await update.message.reply_text("📱 Connecting to Garmin Connect...")
        garmin = _get_garmin_session()
        if not garmin:
            await update.message.reply_text("❌ Failed to connect to Garmin.")
            return

        # Step 2: Get Garmin activities from past 24 hours
        garmin_activities = get_garmin_activities_since(garmin, hours=24)
        if not garmin_activities:
            await update.message.reply_text("❌ No Garmin activities found in the past 24 hours.")
            return

        await update.message.reply_text(f"📋 Found {len(garmin_activities)} Garmin activities in past 24 hours")

        # Step 3: Connect to Strava
        await update.message.reply_text("🏃 Connecting to Strava...")
        client_id, client_secret = get_strava_credentials(interactive=False)
        access_token = get_strava_access_token(client_id, client_secret)

        # Step 4: Get Strava activities
        strava_activities = get_strava_activities(access_token, count=20)
        if not strava_activities:
            await update.message.reply_text("❌ No Strava activities found.")
            return

        # Step 5: Process each Garmin activity
        synced_count = 0
        skipped_count = 0
        not_found_count = 0
        results = []

        for garmin_activity in garmin_activities:
            garmin_name = garmin_activity.get("activityName", "Unknown")
            garmin_time = garmin_activity.get("startTimeLocal", "Unknown")

            # Find matching Strava activity
            matching_strava = find_matching_strava_activity(garmin_activity, strava_activities)

            if not matching_strava:
                not_found_count += 1
                results.append(f"❓ {garmin_name} - No match found")
                continue

            strava_name = matching_strava.get("name", "Unknown")
            strava_id = matching_strava.get("id")

            # Check if update needed
            if strava_name == garmin_name:
                skipped_count += 1
                results.append(f"✅ {garmin_name} - Already synced")
                continue

            # Update Strava
            try:
                update_strava_activity(access_token, strava_id, garmin_name)
                synced_count += 1
                results.append(f"🔄 {strava_name} → {garmin_name}")
                time.sleep(1.5)  # Delay between updates to avoid Strava rate limits
            except Exception as e:
                results.append(f"❌ {garmin_name} - Error: {e}")

        # Send summary
        summary = (
            f"*Sync Complete!*\n\n"
            f"✅ Synced: {synced_count}\n"
            f"⏭️ Already matched: {skipped_count}\n"
            f"❓ No match found: {not_found_count}\n\n"
            f"*Details:*\n" + "\n".join(results)
        )

        await update.message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Error during sync")
        await update.message.reply_text(f"❌ Error: {e}")


async def sync_last_10(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync_last_10 command - sync the last 10 Garmin activities to Strava."""
    if not _is_allowed(update):
        return
    global _last_sync_time

    # Debounce: reject if last sync was too recent
    elapsed = time.time() - _last_sync_time
    if elapsed < SYNC_COOLDOWN_SECONDS:
        remaining = int(SYNC_COOLDOWN_SECONDS - elapsed)
        await update.message.reply_text(f"⏳ Please wait {remaining}s before syncing again.")
        return

    await update.message.reply_text("🔄 Syncing last 10 activities...")
    _last_sync_time = time.time()

    try:
        # Step 1: Connect to Garmin (uses cached session)
        await update.message.reply_text("📱 Connecting to Garmin Connect...")
        garmin = _get_garmin_session()
        if not garmin:
            await update.message.reply_text("❌ Failed to connect to Garmin.")
            return

        # Step 2: Get last 10 Garmin activities
        garmin_activities = get_garmin_activities(garmin, count=10)
        if not garmin_activities:
            await update.message.reply_text("❌ No Garmin activities found.")
            return

        await update.message.reply_text(f"📋 Found {len(garmin_activities)} Garmin activities")

        # Step 3: Connect to Strava
        await update.message.reply_text("🏃 Connecting to Strava...")
        client_id, client_secret = get_strava_credentials(interactive=False)
        access_token = get_strava_access_token(client_id, client_secret)

        # Step 4: Get Strava activities
        strava_activities = get_strava_activities(access_token, count=20)
        if not strava_activities:
            await update.message.reply_text("❌ No Strava activities found.")
            return

        # Step 5: Process each Garmin activity
        synced_count = 0
        skipped_count = 0
        not_found_count = 0
        results = []

        for garmin_activity in garmin_activities:
            garmin_name = garmin_activity.get("activityName", "Unknown")

            # Find matching Strava activity
            matching_strava = find_matching_strava_activity(garmin_activity, strava_activities)

            if not matching_strava:
                not_found_count += 1
                results.append(f"❓ {garmin_name} - No match")
                continue

            strava_name = matching_strava.get("name", "Unknown")
            strava_id = matching_strava.get("id")

            # Check if update needed
            if strava_name == garmin_name:
                skipped_count += 1
                results.append(f"✅ {garmin_name}")
                continue

            # Update Strava
            try:
                update_strava_activity(access_token, strava_id, garmin_name)
                synced_count += 1
                results.append(f"🔄 {strava_name} → {garmin_name}")
                time.sleep(1.5)  # Delay between updates to avoid Strava rate limits
            except Exception as e:
                results.append(f"❌ {garmin_name} - Error")

        # Send summary
        summary = (
            f"*Sync Complete!*\n\n"
            f"✅ Synced: {synced_count}\n"
            f"⏭️ Already matched: {skipped_count}\n"
            f"❓ No match: {not_found_count}\n\n"
            f"*Details:*\n" + "\n".join(results)
        )

        await update.message.reply_text(summary, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Error during sync")
        await update.message.reply_text(f"❌ Error: {e}")


def main():
    """Start the bot."""
    # Get bot token
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        token = input("Enter Telegram Bot Token: ").strip()

    if not token:
        print("❌ No bot token provided. Exiting.")
        sys.exit(1)

    print("🤖 Starting Strava Sync Bot...")
    print("   Press Ctrl+C to stop.\n")

    # Create application and add handlers
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sync", sync))
    application.add_handler(CommandHandler("sync_last_10", sync_last_10))

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped.")
