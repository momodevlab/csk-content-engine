"""
CSK Tech Solutions — Friday Newsletter Pipeline
Runs every Friday at 7:30 AM CST via GitHub Actions.

Flow:
1. Collect all newsletter sections from the week's Track 1 content
2. Collect top 3 Track 2 stories from the week
3. Compile full newsletter issue with Claude
4. Generate 3 subject line options, use first
5. Send via GHL Email API
6. Save compiled issue to newsletter/
7. Generate weekly hero video from top idea's scene manifest (if available)
8. Post confirmation to #content-performance
"""

import os
import json
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv
from slack_sdk import WebClient

from newsletter_builder import run_friday_newsletter
from heygen_video import generate_weekly_video
from utils.logger import get_logger

load_dotenv()
logger = get_logger("main_friday")


def _find_top_manifest(week_start: str) -> tuple[str | None, str | None]:
    """
    Searches the week's content directories for a video_scene_manifest.json.
    Returns (manifest_path, date_str) of the first one found, or (None, None).
    """
    start = date.fromisoformat(week_start)
    for offset in range(5):  # Mon-Fri
        day_str = (start + timedelta(days=offset)).isoformat()
        manifest_path = Path(f"content/{day_str}/track1/video_scene_manifest.json")
        if manifest_path.exists():
            return str(manifest_path), day_str
    return None, None


def run_friday():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    week_start = monday.isoformat()

    logger.info(f"=== CSK Newsletter Pipeline: week of {week_start} ===")

    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    perf_channel = os.environ["SLACK_PERFORMANCE_CHANNEL_ID"]

    # Step 1-6: Newsletter
    try:
        result = run_friday_newsletter(week_start)
        logger.info(f"Newsletter sent: {result['subject']}")
    except Exception as e:
        logger.error(f"Newsletter pipeline failed: {e}")
        slack.chat_postMessage(
            channel=perf_channel,
            text=f"⚠️ Newsletter pipeline failed: {str(e)[:300]}\nManual send needed.",
        )
        result = None

    # Step 7: Weekly hero video from scene manifest
    manifest_path, manifest_date = _find_top_manifest(week_start)
    if manifest_path:
        logger.info(f"Found scene manifest from {manifest_date} — generating weekly hero video...")
        try:
            out_dir = Path(f"content/{week_start}/weekly_video")
            out_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(out_dir / "weekly_hero.mp4")
            generate_weekly_video(manifest_path, output_path)
            slack.chat_postMessage(
                channel=perf_channel,
                text=f"🎬 Weekly hero video rendered → {output_path}",
            )
        except Exception as e:
            logger.error(f"Weekly video generation failed: {e}")
            slack.chat_postMessage(
                channel=perf_channel,
                text=f"⚠️ Weekly video generation failed: {str(e)[:200]}",
            )
    else:
        logger.info("No scene manifest found for this week — skipping weekly video")

    # Step 8: Post newsletter confirmation
    if result:
        slack.chat_postMessage(
            channel=perf_channel,
            text=(
                f"📧 Newsletter sent!\n"
                f"Subject: {result['subject']}\n"
                f"Saved to: {result['saved_to']}"
            ),
        )


if __name__ == "__main__":
    run_friday()
