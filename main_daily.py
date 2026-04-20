"""
CSK Tech Solutions — Daily Content Pipeline (Track 1)
Runs at 7:00 AM CST every weekday via GitHub Actions.

Flow:
1. Scrape ideas from 8 sources (Reddit, HN, Google Trends, YouTube, Twitter/Apify,
   TikTok/Apify, Instagram/Apify, Quora)
2. Score and rank ideas
3. Take top 3 ideas → generate full content packages
4. For top idea: generate video scene manifest (weekly hero video prep)
5. For top 3 ideas with score >= 7: generate HeyGen daily video clips
6. Post all content to #content-approval in Slack
7. Auto-approval checker runs after 24 hours via GitHub Actions
"""

import os
import json
from datetime import date
from dotenv import load_dotenv
from slack_sdk import WebClient

from idea_scraper import run_idea_scraper
from content_creator import create_full_content_package, generate_scene_manifest
from content_publisher import (
    post_track1_for_approval,
    post_video_for_approval,
    check_reactions,
    check_auto_approvals,
    handle_approval,
)
from heygen_video import create_avatar_video
from caption_video import add_captions, check_dependencies
from utils.logger import get_logger

load_dotenv()
logger = get_logger("main_daily")


def run_daily(dry_run: bool = False):
    date_str = date.today().isoformat()
    logger.info(f"=== CSK Daily Pipeline starting: {date_str} {'(DRY RUN)' if dry_run else ''} ===")

    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    approval_channel = os.environ["SLACK_APPROVAL_CHANNEL_ID"]

    # Step 1: Scrape and score ideas
    logger.info("Step 1: Scraping ideas...")
    top_ideas = run_idea_scraper(date_str)
    if not top_ideas:
        logger.warning("No ideas scraped today — posting alert to Slack")
        if not dry_run:
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
        if not dry_run:
            slack.chat_postMessage(
                channel=approval_channel,
                text="⚠️ Content package creation failed for all ideas today. Manual content needed.",
            )
        return

    # Step 3: Generate scene manifest for top idea (weekly hero video prep)
    top_idea = top_ideas[0]
    top_package = packages[0] if packages else None
    if top_package:
        video_script = top_package.get("content", {}).get("video_script", "")
        if video_script:
            logger.info("Step 3: Generating video scene manifest for top idea...")
            try:
                generate_scene_manifest(
                    script=video_script,
                    topic=top_idea.get("title", ""),
                    industry=top_idea.get("csk_angle_note", "business automation"),
                    date_str=date_str,
                )
            except Exception as e:
                logger.error(f"Scene manifest generation failed: {e}")

    # Step 4: Post to Slack for approval
    if not dry_run:
        logger.info(f"Step 4: Posting {len(packages)} packages to #content-approval...")
        for package in packages:
            try:
                post_track1_for_approval(package, slack, approval_channel)
            except Exception as e:
                logger.error(f"Slack approval post failed: {e}")
    else:
        logger.info(f"Step 4: DRY RUN — skipping Slack posts for {len(packages)} packages")

    # Step 5: HeyGen daily videos for top 3 ideas with score >= 7
    logger.info("Step 5: Generating HeyGen daily videos for qualifying ideas...")
    try:
        check_dependencies()
    except Exception as e:
        logger.warning(f"Caption dependencies not available: {e}")

    for i, (idea, package) in enumerate(zip(top_ideas[:3], packages)):
        score = idea.get("scores", {}).get("total", 0)
        if score < 7:
            logger.info(f"Idea {i+1} score {score} < 7 — skipping video")
            continue

        video_script = package.get("content", {}).get("video_script", "")
        if not video_script:
            logger.info(f"Idea {i+1} has no video script — skipping video")
            continue

        logger.info(f"Generating video for idea {i+1}: {idea['title'][:60]}")
        try:
            video_result = create_avatar_video(idea, date_str, video_script)
            if video_result and not dry_run:
                captioned_path = add_captions(video_result["raw_video_path"], date_str)
                video_result["captioned_path"] = captioned_path
                post_video_for_approval(video_result, slack, approval_channel)
        except Exception as e:
            logger.error(f"Video pipeline failed for idea {i+1}: {e}")
            if not dry_run:
                slack.chat_postMessage(
                    channel=approval_channel,
                    text=f"⚠️ HeyGen video pipeline failed for idea {i+1}: {str(e)[:200]}",
                )

    logger.info(
        f"=== Daily pipeline {'(DRY RUN) ' if dry_run else ''}complete. "
        f"{len(packages)} content packages {'ready for approval' if not dry_run else 'generated locally'}. ==="
    )

    # Dry run: print a summary of what was generated
    if dry_run:
        for i, package in enumerate(packages, 1):
            files = list(package.get("files", {}).keys())
            print(f"\nPackage {i}: {top_ideas[i-1]['title'][:60]}")
            print(f"  Formats: {', '.join(files)}")


if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    run_daily(dry_run=dry)
