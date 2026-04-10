"""
CSK Tech Solutions — AI News Feed Pipeline (Track 2)
Runs every 4 hours via GitHub Actions.

Flow:
1. Scrape 20+ RSS feeds + HN + Reddit
2. Deduplicate against seen_stories.json
3. Score each story
4. Score 7+: create content → auto-post to LinkedIn + Twitter
5. Score 5-6: create content → post to #content-approval
6. Score <5: log and skip
7. Post FYI log to #ai-news-feed
"""

import os
from datetime import date
from dotenv import load_dotenv
from slack_sdk import WebClient

from news_scraper import run_news_scraper
from content_creator import create_news_content_package
from content_publisher import (
    post_track2_for_review,
    log_auto_post,
    publish_linkedin_post,
    publish_twitter_thread,
    _add_pending,
)
from utils.logger import get_logger

load_dotenv()
logger = get_logger("main_news")


def run_news():
    date_str = date.today().isoformat()
    logger.info(f"=== CSK News Pipeline starting: {date_str} ===")

    slack = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    approval_channel = os.environ["SLACK_APPROVAL_CHANNEL_ID"]
    news_channel = os.environ["SLACK_NEWS_CHANNEL_ID"]

    # Step 1: Scrape and score
    results = run_news_scraper(date_str)
    logger.info(
        f"Scraped: {len(results['auto_post'])} auto-post, "
        f"{len(results['slack_review'])} for review, "
        f"{results['skipped']} skipped"
    )

    # Step 2: Handle auto-post stories (score 7+)
    for story in results["auto_post"]:
        logger.info(f"Auto-posting: {story['title'][:60]}")
        try:
            content_package = create_news_content_package(story, date_str)
            if not content_package:
                continue
            content = content_package.get("content", {})

            linkedin_text = content.get("news_linkedin", "")
            if linkedin_text:
                publish_linkedin_post(linkedin_text)

            twitter_tweets = content.get("news_twitter", [])
            if twitter_tweets:
                publish_twitter_thread(twitter_tweets)

            log_auto_post(story, slack, news_channel)
        except Exception as e:
            logger.error(f"Auto-post failed for {story['story_id']}: {e}")

    # Step 3: Handle review stories (score 5-6)
    for story in results["slack_review"]:
        logger.info(f"Sending for review: {story['title'][:60]}")
        try:
            content_package = create_news_content_package(story, date_str)
            if not content_package:
                continue
            content = content_package.get("content", {})
            ts = post_track2_for_review(story, content, slack, approval_channel)
            if ts:
                _add_pending(ts, "track2", story["story_id"])
        except Exception as e:
            logger.error(f"Review post failed for {story['story_id']}: {e}")

    logger.info("=== News pipeline complete ===")


if __name__ == "__main__":
    run_news()
