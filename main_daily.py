"""
CSK Tech Solutions — Daily Content Pipeline (Track 1)
Runs at 7:00 AM CST every weekday via GitHub Actions.

Flow:
1. Scrape ideas from 6 sources
2. Score and rank ideas
3. Take top 3 ideas
4. Create full content package for each (LinkedIn, Twitter, blog, newsletter, carousel)
5. For top idea (score 7+): generate HeyGen video script → render → caption
6. Post all content to #content-approval in Slack
7. Auto-approval checker runs after 24 hours via GitHub Actions
"""

import os
import json
from datetime import date
from dotenv import load_dotenv
from slack_sdk import WebClient

from idea_scraper import run_idea_scraper
from content_creator import create_full_content_package
from content_publisher import (
    post_track1_for_approval,
    post_video_for_approval,
    check_reactions,
    check_auto_approvals,
    handle_approval,
    _add_pending,
)
from heygen_video import create_avatar_video
from caption_video import add_captions, check_dependencies
from utils.logger import get_logger

load_dotenv()
logger = get_logger("main_daily")


def run_daily():
    date_str = date.today().isoformat()
    logger.info(f"=== CSK Daily Pipeline starting: {date_str} ===")

    # Init Slack
    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    approval_channel = os.environ["SLACK_APPROVAL_CHANNEL_ID"]

    # Step 1: Scrape and score ideas
    logger.info("Step 1: Scraping ideas...")
    top_ideas = run_idea_scraper(date_str)
    if not top_ideas:
        logger.warning("No ideas scraped today — posting alert to Slack")
        slack.chat_postMessage(
            channel=approval_channel,
            text="⚠️ Daily scraper returned no ideas today. Manual content needed.",
        )
        return

    scores = [i["scores"]["total"] for i in top_ideas]
    logger.info(f"Got {len(top_ideas)} top ideas. Scores: {scores}")

    # Step 2: Create content packages for top 3
    logger.info("Step 2: Creating content packages...")
    packages = []
    for idea in top_ideas[:3]:
        logger.info(f"Creating package for: {idea['title'][:60]}")
        try:
            package = create_full_content_package(idea, date_str)
            package["date_str"] = date_str
            if package:
                packages.append(package)
        except Exception as e:
            logger.error(f"Package creation failed for '{idea['title'][:60]}': {e}")

    if not packages:
        slack.chat_postMessage(
            channel=approval_channel,
            text="⚠️ Content package creation failed for all ideas today. Manual content needed.",
        )
        return

    # Step 3: Post to Slack for approval
    logger.info(f"Step 3: Posting {len(packages)} packages to #content-approval...")
    for package in packages:
        try:
            ts = post_track1_for_approval(package, slack, approval_channel)
            if ts:
                _add_pending(ts, "track1", date_str)
        except Exception as e:
            logger.error(f"Slack approval post failed: {e}")

    # Step 4: HeyGen video for top idea (score >= 7 only)
    top_idea = top_ideas[0]
    if top_idea["scores"]["total"] >= 7:
        logger.info(
            f"Step 4: Generating HeyGen video for top idea "
            f"(score {top_idea['scores']['total']})..."
        )
        try:
            check_dependencies()
            video_result = create_avatar_video(top_idea, date_str)
            if video_result:
                captioned_path = add_captions(video_result["raw_video_path"], date_str)
                video_result["captioned_path"] = captioned_path
                video_result["content_id"] = date_str
                ts = post_video_for_approval(video_result, slack, approval_channel)
                if ts:
                    _add_pending(ts, "video", date_str)
        except Exception as e:
            logger.error(f"Video pipeline failed: {e}")
            slack.chat_postMessage(
                channel=approval_channel,
                text=f"⚠️ HeyGen video pipeline failed: {str(e)[:200]}",
            )
    else:
        logger.info(
            f"Top idea score {top_idea['scores']['total']} < 7 — skipping video"
        )

    logger.info(
        f"=== Daily pipeline complete. "
        f"{len(packages)} content packages ready for approval. ==="
    )


if __name__ == "__main__":
    run_daily()
