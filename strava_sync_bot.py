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

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Load environment variables from .env file
load_dotenv()

# Import sync functions from existing script
from sync_garmin_to_strava import (
    init_garmin_api,
    get_garmin_latest_activity,
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "🔄 *Garmin to Strava Sync Bot*\n\n"
        "Commands:\n"
        "/sync - Sync latest Garmin activity title to Strava\n\n"
        "This bot reads your latest Garmin activity and updates "
        "the matching Strava activity with the same title.",
        parse_mode="Markdown"
    )


async def sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync command - sync latest Garmin activity title to Strava."""
    await update.message.reply_text("🔄 Starting sync...")

    try:
        # Step 1: Connect to Garmin
        await update.message.reply_text("📱 Connecting to Garmin Connect...")
        garmin = init_garmin_api()
        if not garmin:
            await update.message.reply_text("❌ Failed to connect to Garmin.")
            return

        # Step 2: Get latest Garmin activity
        garmin_activity = get_garmin_latest_activity(garmin)
        if not garmin_activity:
            await update.message.reply_text("❌ No Garmin activities found.")
            return

        garmin_name = garmin_activity.get("activityName", "Unknown")
        garmin_time = garmin_activity.get("startTimeLocal", "Unknown")
        garmin_distance = garmin_activity.get("distance", 0) / 1000

        await update.message.reply_text(
            f"📋 *Latest Garmin Activity:*\n"
            f"• Name: {garmin_name}\n"
            f"• Time: {garmin_time}\n"
            f"• Distance: {garmin_distance:.2f} km",
            parse_mode="Markdown"
        )

        # Step 3: Connect to Strava
        await update.message.reply_text("🏃 Connecting to Strava...")
        client_id, client_secret = get_strava_credentials(interactive=False)
        access_token = get_strava_access_token(client_id, client_secret)

        # Step 4: Get Strava activities and find match
        strava_activities = get_strava_activities(access_token, count=10)
        if not strava_activities:
            await update.message.reply_text("❌ No Strava activities found.")
            return

        matching_strava = find_matching_strava_activity(garmin_activity, strava_activities)
        if not matching_strava:
            await update.message.reply_text(
                "❌ No matching Strava activity found.\n\n"
                "Recent Strava activities:\n" +
                "\n".join([f"• {a.get('name')}" for a in strava_activities[:5]])
            )
            return

        strava_name = matching_strava.get("name", "Unknown")
        strava_id = matching_strava.get("id")
        strava_distance = matching_strava.get("distance", 0) / 1000

        # Step 5: Check if update needed
        if strava_name == garmin_name:
            await update.message.reply_text(
                f"✅ Titles already match!\n"
                f"📝 Title: {strava_name}"
            )
            return

        # Step 6: Update Strava
        await update.message.reply_text(
            f"📝 *Updating title:*\n"
            f"• From: {strava_name}\n"
            f"• To: {garmin_name}",
            parse_mode="Markdown"
        )

        updated = update_strava_activity(access_token, strava_id, garmin_name)

        await update.message.reply_text(
            f"✅ *Success!*\n\n"
            f"Old: {strava_name}\n"
            f"New: {updated.get('name')}\n\n"
            f"🔗 [View on Strava](https://www.strava.com/activities/{strava_id})",
            parse_mode="Markdown"
        )

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

    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Bot stopped.")
