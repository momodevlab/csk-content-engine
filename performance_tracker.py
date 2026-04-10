"""
performance_tracker.py — CSK Content Engine

Runs every Monday at 8 AM CST. Pulls last week's analytics from LinkedIn
(Playwright scraper), Twitter/X (API v2), and GHL newsletter stats.
Analyzes with Claude and posts a formatted report to #content-performance.

Session note: LinkedIn scraping requires a stored session cookie file
(linkedin_session.json). If the session is expired or missing, the scraper
logs a warning and reports "LinkedIn data unavailable" without crashing.
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright
from anthropic import Anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

from utils.logger import get_logger
from utils.rate_limiter import polite_delay

load_dotenv()
logger = get_logger("performance_tracker")

PUBLISHED_CONTENT_PATH = Path("published_content.json")
LINKEDIN_SESSION_PATH  = Path("linkedin_session.json")

GHL_BASE_URL    = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

DIVIDER = "─" * 26


# ---------------------------------------------------------------------------
# Published content store
# ---------------------------------------------------------------------------

def load_published_content() -> list[dict]:
    """
    Loads published_content.json from project root.
    This file is appended to by content_publisher.py after every successful publish.
    Returns empty list if missing.
    """
    if not PUBLISHED_CONTENT_PATH.exists():
        return []
    try:
        with open(PUBLISHED_CONTENT_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load published_content.json: {e}")
        return []


def get_last_week_content(week_start: str) -> list[dict]:
    """
    Filters published_content.json to entries from the given week (Mon–Sun).
    week_start: "2026-03-23" (previous Monday)
    Returns list of published content entries.
    """
    start_dt = datetime.strptime(week_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=7)
    all_content = load_published_content()
    return [
        entry for entry in all_content
        if start_dt <= datetime.strptime(entry["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) < end_dt
    ]


# ---------------------------------------------------------------------------
# LinkedIn analytics (Playwright)
# ---------------------------------------------------------------------------

def _load_linkedin_session() -> "Optional[List[dict]]":
    """
    Loads stored LinkedIn session cookies from linkedin_session.json.
    Returns the cookies list or None if the file doesn't exist.

    Session management notes:
    - linkedin_session.json is NOT committed to git (add to .gitignore)
    - To create it: log in to LinkedIn in a Playwright browser, export cookies
      via context.cookies(), and save to this file
    - Sessions typically last 1-2 weeks; when expired, scraper falls back gracefully
    - To refresh: re-run the session capture script (not part of this pipeline)
    - Store the file in the project root alongside other persistent JSON files
    """
    if not LINKEDIN_SESSION_PATH.exists():
        return None
    try:
        with open(LINKEDIN_SESSION_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load LinkedIn session: {e}")
        return None


def scrape_linkedin_analytics(posts_published: list[dict]) -> "Optional[List[dict]]":
    """
    Uses Playwright headless Chromium with stored session cookies to scrape
    analytics for each LinkedIn post published last week.
    Also scrapes the profile page for weekly profile views and new followers.

    For each post URL, navigates to the post and extracts:
    - impressions, reactions (likes), comments, reposts

    Profile-level metrics are scraped from LinkedIn Analytics tab.
    Uses 3-4 second polite delays between every page load.

    Returns list of post analytics dicts, or None if session is expired/missing.
    Session expiry is detected if LinkedIn redirects to the login page.
    """
    cookies = _load_linkedin_session()
    if not cookies:
        logger.warning("No LinkedIn session file found — skipping LinkedIn analytics")
        return None

    linkedin_posts = [p for p in posts_published if p.get("platform") == "linkedin"]
    if not linkedin_posts:
        logger.info("No LinkedIn posts published last week")
        return []

    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies(cookies)
            page = context.new_page()

            # Session validity check
            page.goto("https://www.linkedin.com/feed/", timeout=20000)
            polite_delay(3.0, 4.0)
            if "linkedin.com/login" in page.url or "authwall" in page.url:
                logger.warning("LinkedIn session expired — analytics unavailable")
                browser.close()
                return None

            # Scrape each post
            for post in linkedin_posts:
                post_url = post.get("post_url", "")
                if not post_url:
                    continue
                try:
                    page.goto(post_url, timeout=20000)
                    polite_delay(3.0, 4.0)

                    # Extract engagement metrics from post page
                    # LinkedIn renders metrics in various elements depending on post type
                    impressions = _extract_text_int(page, "[data-test-id='social-counts-reactions']")
                    reactions   = _extract_text_int(page, ".social-counts-reactions__count")
                    comments    = _extract_text_int(page, ".social-counts-comments")
                    reposts     = _extract_text_int(page, ".social-counts-reposts")

                    results.append({
                        "post_url":   post_url,
                        "idea_title": post.get("idea_title", ""),
                        "date":       post.get("date", ""),
                        "impressions": impressions,
                        "reactions":   reactions,
                        "comments":    comments,
                        "reposts":     reposts,
                    })
                    logger.info(f"LinkedIn post scraped: {post_url[:60]}")
                except Exception as e:
                    logger.warning(f"Could not scrape LinkedIn post {post_url[:60]}: {e}")
                    results.append({
                        "post_url":   post_url,
                        "idea_title": post.get("idea_title", ""),
                        "date":       post.get("date", ""),
                        "impressions": None, "reactions": None,
                        "comments": None, "reposts": None,
                        "error": str(e),
                    })

            # Profile-level metrics
            profile_views = None
            new_followers = None
            try:
                page.goto("https://www.linkedin.com/analytics/creator/", timeout=20000)
                polite_delay(3.0, 4.0)
                profile_views = _extract_text_int(page, "[data-test-id='analytics-profile-views']")
                new_followers = _extract_text_int(page, "[data-test-id='analytics-follower-count']")
            except Exception as e:
                logger.warning(f"Could not scrape LinkedIn profile analytics: {e}")

            browser.close()

        return {
            "posts": results,
            "profile_views": profile_views,
            "new_followers": new_followers,
        }

    except Exception as e:
        logger.error(f"LinkedIn Playwright scraper crashed: {e}")
        return None


def _extract_text_int(page, selector: str) -> "Optional[int]":
    """
    Tries to locate a Playwright element by CSS selector and parse its text as int.
    Returns None if not found or unparseable.
    """
    try:
        el = page.query_selector(selector)
        if el:
            text = el.inner_text().strip().replace(",", "").split()[0]
            return int(text)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Twitter/X analytics
# ---------------------------------------------------------------------------

def fetch_twitter_analytics(tweet_ids: list[str]) -> "Optional[List[dict]]":
    """
    Fetches engagement metrics for each tweet ID via Twitter API v2.
    Metrics: impressions, likes, retweets, replies.
    Also fetches the current follower count for the account.

    Returns list of tweet analytics dicts, or None on API failure.
    Falls back gracefully if the bearer token lacks elevated access.
    """
    if not tweet_ids:
        logger.info("No tweet IDs to fetch analytics for")
        return []

    bearer = os.environ.get("TWITTER_BEARER_TOKEN", "")
    if not bearer:
        logger.warning("TWITTER_BEARER_TOKEN not set — skipping Twitter analytics")
        return None

    headers = {"Authorization": f"Bearer {bearer}"}

    results = []
    # Twitter API allows up to 100 IDs per request
    chunks = [tweet_ids[i:i+100] for i in range(0, len(tweet_ids), 100)]

    for chunk in chunks:
        try:
            resp = requests.get(
                "https://api.twitter.com/2/tweets",
                headers=headers,
                params={
                    "ids": ",".join(chunk),
                    "tweet.fields": "public_metrics,created_at,text",
                },
                timeout=15,
            )
            if resp.status_code == 401:
                logger.warning("Twitter API unauthorized — analytics unavailable")
                return None
            resp.raise_for_status()
            data = resp.json()
            for tweet in data.get("data", []):
                metrics = tweet.get("public_metrics", {})
                results.append({
                    "tweet_id":    tweet["id"],
                    "text":        tweet.get("text", "")[:100],
                    "impressions": metrics.get("impression_count"),
                    "likes":       metrics.get("like_count"),
                    "retweets":    metrics.get("retweet_count"),
                    "replies":     metrics.get("reply_count"),
                })
            time.sleep(1)
        except Exception as e:
            logger.warning(f"Twitter analytics batch failed: {e}")

    # Follower count
    new_followers = None
    try:
        username = os.environ.get("TWITTER_USERNAME", "CSKTechSolutions")
        user_resp = requests.get(
            f"https://api.twitter.com/2/users/by/username/{username}",
            headers=headers,
            params={"user.fields": "public_metrics"},
            timeout=10,
        )
        if user_resp.ok:
            new_followers = user_resp.json().get("data", {}).get("public_metrics", {}).get("followers_count")
    except Exception as e:
        logger.warning(f"Could not fetch Twitter follower count: {e}")

    logger.info(f"Twitter analytics fetched for {len(results)} tweets")
    return {"tweets": results, "follower_count": new_followers}


# ---------------------------------------------------------------------------
# Newsletter stats
# ---------------------------------------------------------------------------

def fetch_newsletter_stats(issue_id: str = None) -> dict:
    """
    Attempts to fetch open rate, click rate, and new subscriber count from
    the GHL analytics API for the last sent newsletter.
    Falls back to None values with a note if the API is unavailable.
    """
    headers = {
        "Authorization": f"Bearer {os.environ.get('GHL_API_KEY', '')}",
        "Version": GHL_API_VERSION,
    }
    try:
        endpoint = f"{GHL_BASE_URL}/email/stats"
        params = {"locationId": os.environ.get("GHL_LOCATION_ID", "")}
        if issue_id:
            params["emailId"] = issue_id

        resp = requests.get(endpoint, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            "open_rate":       data.get("openRate"),
            "click_rate":      data.get("clickRate"),
            "new_subscribers": data.get("newSubscribers"),
            "subject":         data.get("subject", ""),
            "available":       True,
        }
    except Exception as e:
        logger.warning(f"Newsletter stats unavailable: {e}")
        return {
            "open_rate":       None,
            "click_rate":      None,
            "new_subscribers": None,
            "subject":         "",
            "available":       False,
            "note":            "GHL stats API unavailable — manual check needed",
        }


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

def analyze_performance(weekly_data: dict) -> dict:
    """
    Sends the week's consolidated performance data to Claude for analysis.
    Returns a dict with:
    - top_performer_analysis: what worked and why
    - lowest_performer_analysis: honest assessment
    - insights: list of pattern observations
    - next_week_recommendations: list of 3 specific action items
    Falls back to placeholder analysis on API failure.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = (
        "Analyze this week's content performance data for CSK Tech Solutions.\n"
        "Identify:\n"
        "1. Top performing post and WHY it worked (be specific — was it the hook? the topic? the format?)\n"
        "2. Lowest performer and why (honest assessment)\n"
        "3. 3 strategic recommendations for next week based on what the data shows\n"
        "4. Any patterns in what content type or topic is driving the most engagement\n\n"
        "Be direct and data-driven. No fluff. Specific observations only.\n"
        "Return ONLY valid JSON with no extra text:\n"
        "{\n"
        '  "top_performer_analysis": "...",\n'
        '  "lowest_performer_analysis": "...",\n'
        '  "insights": ["...", "...", "..."],\n'
        '  "next_week_recommendations": ["...", "...", "..."]\n'
        "}\n\n"
        f"Data:\n{json.dumps(weekly_data, indent=2)[:6000]}"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        import re
        raw = response.content[0].text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
        result = json.loads(cleaned)
        logger.info("Performance analysis complete")
        return result
    except Exception as e:
        logger.error(f"Performance analysis failed: {e}")
        return {
            "top_performer_analysis":    "Analysis unavailable this week.",
            "lowest_performer_analysis": "Analysis unavailable this week.",
            "insights":                  ["Data collected — manual review needed."],
            "next_week_recommendations": ["Review raw data manually.", "Check API connections.", "Retry analysis."],
        }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def build_slack_report(weekly_data: dict, analysis: dict, week_start: str) -> str:
    """
    Assembles the full Slack report string for #content-performance.
    Handles None values gracefully (shows "—" for missing data).
    """
    li   = weekly_data.get("linkedin", {}) or {}
    tw   = weekly_data.get("twitter", {}) or {}
    nl   = weekly_data.get("newsletter", {}) or {}

    li_posts   = li.get("posts", [])
    li_impr    = sum(p.get("impressions") or 0 for p in li_posts)
    li_react   = sum(p.get("reactions") or 0 for p in li_posts)
    li_comm    = sum(p.get("comments") or 0 for p in li_posts)
    li_views   = li.get("profile_views", "—")
    li_follow  = li.get("new_followers", "—")
    li_unavail = li_posts is None

    tw_tweets  = tw.get("tweets", [])
    tw_impr    = sum(t.get("impressions") or 0 for t in tw_tweets)
    tw_eng     = sum((t.get("likes") or 0) + (t.get("retweets") or 0) + (t.get("replies") or 0) for t in tw_tweets)
    tw_follow  = tw.get("follower_count", "—")
    tw_unavail = tw_tweets is None

    nl_open   = f"{nl.get('open_rate', '—')}%" if nl.get("open_rate") is not None else "—"
    nl_click  = f"{nl.get('click_rate', '—')}%" if nl.get("click_rate") is not None else "—"
    nl_new    = nl.get("new_subscribers", "—") or "—"
    nl_subj   = nl.get("subject", "—") or "—"

    top    = analysis.get("top_performer_analysis", "—")
    low    = analysis.get("lowest_performer_analysis", "—")
    recs   = analysis.get("next_week_recommendations", [])
    recs_text = "\n".join(f"• {r}" for r in recs) if recs else "• No recommendations generated"

    # Find top post for preview
    top_post = max(li_posts, key=lambda p: p.get("impressions") or 0) if li_posts else {}
    top_preview = top_post.get("idea_title", "—")[:100]
    top_impr = top_post.get("impressions", "—")
    top_react = top_post.get("reactions", "—")

    low_post = min(li_posts, key=lambda p: p.get("impressions") or 0) if li_posts else {}
    low_preview = low_post.get("idea_title", "—")[:80]

    li_section = (
        "LinkedIn data unavailable — session expired, manual check needed"
        if li_unavail else
        f"Posts published: {len(li_posts)}\n"
        f"Total impressions: {li_impr:,}\n"
        f"Total reactions: {li_react:,}\n"
        f"Comments: {li_comm:,}\n"
        f"Profile views: {li_views}\n"
        f"New followers: {li_follow}"
    )

    tw_section = (
        "Twitter/X data unavailable — API check needed"
        if tw_unavail else
        f"Tweets/threads: {len(tw_tweets)}\n"
        f"Total impressions: {tw_impr:,}\n"
        f"Total engagements: {tw_eng:,}\n"
        f"Followers: {tw_follow}"
    )

    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    report = (
        f"📊 *WEEKLY CONTENT REPORT — Week of {week_start}*\n"
        f"Posted by CSK Content Engine at {now_str}\n\n"
        f"{DIVIDER}\n"
        f"💼 *LINKEDIN*\n{li_section}\n\n"
        f"🐦 *TWITTER/X*\n{tw_section}\n\n"
        f"📧 *NEWSLETTER*\n"
        f'Subject: "{nl_subj}"\n'
        f"Open rate: {nl_open} (industry avg: 35%)\n"
        f"Click rate: {nl_click}\n"
        f"New subscribers: {nl_new}\n\n"
        f"{DIVIDER}\n"
        f"🏆 *TOP PERFORMER*\n"
        f"{top_preview}\n"
        f"Impressions: {top_impr} | Reactions: {top_react}\n"
        f"Why it worked: {top}\n\n"
        f"📉 *LOWEST PERFORMER*\n"
        f"{low_preview}\n"
        f"Why it underperformed: {low}\n\n"
        f"{DIVIDER}\n"
        f"🔮 *NEXT WEEK STRATEGY*\n{recs_text}\n\n"
        f"{DIVIDER}\n"
        f"Full data saved to performance/{week_start}/weekly_report.md"
    )

    return report


# ---------------------------------------------------------------------------
# Save raw data and report
# ---------------------------------------------------------------------------

def save_performance_data(weekly_data: dict, analysis: dict, report: str, week_start: str) -> str:
    """
    Saves raw_data.json and weekly_report.md to performance/{week_start}/.
    Returns the directory path.
    """
    out_dir = Path(f"performance/{week_start}")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "raw_data.json"
    with open(raw_path, "w") as f:
        json.dump({"weekly_data": weekly_data, "analysis": analysis}, f, indent=2)

    report_path = out_dir / "weekly_report.md"
    report_path.write_text(report)

    logger.info(f"Performance data saved → {out_dir}")
    return str(out_dir)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _last_week_monday() -> str:
    """Returns the ISO date string for last week's Monday."""
    today = datetime.now(timezone.utc)
    last_monday = today - timedelta(days=today.weekday() + 7)
    return last_monday.strftime("%Y-%m-%d")


def run_monday_report(week_start: str = None) -> None:
    """
    Full pipeline: collect analytics → analyze with Claude → build report → post to Slack → save.

    Execution order:
    1. Determine week_start (defaults to last week's Monday)
    2. Load published_content.json and filter to last week's entries
    3. Scrape LinkedIn analytics via Playwright (falls back gracefully on session expiry)
    4. Fetch Twitter analytics via API v2 (falls back gracefully on auth failure)
    5. Fetch newsletter stats from GHL (falls back gracefully)
    6. Save raw_data.json before analysis
    7. Analyze with Claude
    8. Build Slack report string
    9. Post to #content-performance
    10. Save weekly_report.md

    Never fails silently — always posts something to #content-performance even if partial.
    """
    if week_start is None:
        week_start = _last_week_monday()

    logger.info(f"=== Monday performance report starting — week of {week_start} ===")

    # 2. Load last week's published content
    week_content = get_last_week_content(week_start)
    logger.info(f"Found {len(week_content)} published items from last week")

    li_post_entries  = [e for e in week_content if e.get("platform") == "linkedin"]
    tweet_id_list    = [tid for e in week_content for tid in (e.get("tweet_ids") or [])]

    # 3. LinkedIn
    linkedin_data = None
    try:
        linkedin_data = scrape_linkedin_analytics(li_post_entries)
    except Exception as e:
        logger.error(f"LinkedIn analytics crashed: {e}")

    # 4. Twitter
    twitter_data = None
    try:
        twitter_data = fetch_twitter_analytics(tweet_id_list)
    except Exception as e:
        logger.error(f"Twitter analytics crashed: {e}")

    # 5. Newsletter
    newsletter_data = {}
    try:
        newsletter_data = fetch_newsletter_stats()
    except Exception as e:
        logger.error(f"Newsletter stats crashed: {e}")
        newsletter_data = {
            "open_rate": None, "click_rate": None,
            "new_subscribers": None, "subject": "", "available": False,
        }

    # Build consolidated weekly_data
    weekly_data = {
        "week_start":  week_start,
        "linkedin":    linkedin_data,
        "twitter":     twitter_data,
        "newsletter":  newsletter_data,
        "post_count":  len(week_content),
    }

    # 6. Save raw data before analysis
    out_dir = Path(f"performance/{week_start}")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "raw_data.json", "w") as f:
        json.dump(weekly_data, f, indent=2)
    logger.info(f"Raw data saved → {out_dir / 'raw_data.json'}")

    # 7. Analyze
    analysis = {}
    try:
        analysis = analyze_performance(weekly_data)
    except Exception as e:
        logger.error(f"Analysis crashed: {e}")
        analysis = {
            "top_performer_analysis":    "Analysis unavailable.",
            "lowest_performer_analysis": "Analysis unavailable.",
            "insights":                  [],
            "next_week_recommendations": ["Manual data review recommended."],
        }

    # 8. Build report
    report = build_slack_report(weekly_data, analysis, week_start)

    # 9. Post to Slack
    try:
        slack_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel_id = os.environ.get("SLACK_PERFORMANCE_CHANNEL_ID", "")
        slack_client.chat_postMessage(channel=channel_id, text=report)
        logger.info("Performance report posted to #content-performance")
    except SlackApiError as e:
        logger.error(f"Failed to post report to Slack: {e}")

    # 10. Save report
    save_performance_data(weekly_data, analysis, report, week_start)
    logger.info(f"=== Monday report complete ===")


if __name__ == "__main__":
    run_monday_report()
