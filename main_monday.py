"""
CSK Tech Solutions — Monday Performance Report
Runs every Monday at 8:00 AM CST via GitHub Actions.

Flow:
1. Calculate last week's date range
2. Load published_content.json to get post URLs/IDs
3. Pull LinkedIn analytics via Playwright
4. Pull Twitter analytics via API
5. Pull newsletter stats via GHL
6. Analyze with Claude
7. Post report to #content-performance
8. Save report to performance/
"""

import os
from datetime import date, timedelta
from dotenv import load_dotenv
from slack_sdk import WebClient

from performance_tracker import run_monday_report
from utils.logger import get_logger

load_dotenv()
logger = get_logger("main_monday")


def run_monday():
    today = date.today()
    last_monday = (today - timedelta(days=7)).isoformat()

    logger.info(f"=== CSK Performance Report: week of {last_monday} ===")

    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    perf_channel = os.environ["SLACK_PERFORMANCE_CHANNEL_ID"]

    try:
        run_monday_report(week_start=last_monday)
        logger.info("Performance report complete")
    except Exception as e:
        logger.error(f"Performance report failed: {e}")
        slack.chat_postMessage(
            channel=perf_channel,
            text=f"⚠️ Performance report failed: {str(e)[:300]}",
        )


if __name__ == "__main__":
    run_monday()
