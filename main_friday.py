"""
CSK Tech Solutions — Friday Newsletter Pipeline
Runs every Friday at 8:00 AM CST via GitHub Actions.

Flow:
1. Collect all newsletter sections from the week's Track 1 content
2. Collect top 3 Track 2 stories from the week
3. Compile full newsletter issue with Claude
4. Generate 3 subject line options, use first
5. Send via GHL Email API
6. Save compiled issue to newsletter/
7. Post confirmation to #content-performance
"""

import os
from datetime import date, timedelta
from dotenv import load_dotenv
from slack_sdk import WebClient

from newsletter_builder import run_friday_newsletter
from utils.logger import get_logger

load_dotenv()
logger = get_logger("main_friday")


def run_friday():
    # Calculate this week's Monday
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_start = monday.isoformat()

    logger.info(f"=== CSK Newsletter Pipeline: week of {week_start} ===")

    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    perf_channel = os.environ["SLACK_PERFORMANCE_CHANNEL_ID"]

    try:
        result = run_friday_newsletter(week_start)
        slack.chat_postMessage(
            channel=perf_channel,
            text=(
                f"📧 Newsletter sent!\n"
                f"Subject: {result['subject']}\n"
                f"Saved to: {result['saved_to']}"
            ),
        )
        logger.info(f"Newsletter sent: {result['subject']}")
    except Exception as e:
        logger.error(f"Newsletter pipeline failed: {e}")
        slack.chat_postMessage(
            channel=perf_channel,
            text=f"⚠️ Newsletter pipeline failed: {str(e)[:300]}\nManual send needed.",
        )


if __name__ == "__main__":
    run_friday()
