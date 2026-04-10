"""
news_scraper.py — CSK Content Engine

Runs every 4 hours. Scrapes 20+ AI/automation news sources (RSS feeds,
Hacker News, Reddit), deduplicates against a 72-hour rolling window,
scores each story with Claude, and routes to auto-post or Slack review.
"""

import os
import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.logger import get_logger
from utils.rate_limiter import polite_delay, api_delay

load_dotenv()
logger = get_logger("news_scraper")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RSS_FEEDS = {
    "OpenAI Blog":             "https://openai.com/blog/rss.xml",
    "Anthropic Blog":          "https://www.anthropic.com/rss.xml",
    "Google DeepMind Blog":    "https://deepmind.google/blog/rss/",
    "Hugging Face Blog":       "https://huggingface.co/blog/feed.xml",
    "Meta AI Blog":            "https://ai.meta.com/blog/rss/",
    "MIT Technology Review":   "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "VentureBeat AI":          "https://venturebeat.com/category/ai/feed/",
    "TechCrunch AI":           "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI":            "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "Wired AI":                "https://www.wired.com/feed/tag/ai/latest/rss",
    "ZDNet AI":                "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",
    "The Batch":               "https://www.deeplearning.ai/the-batch/feed/",
    "Import AI":               "https://importai.substack.com/feed",
    "AI Breakfast":            "https://aibreakfast.beehiiv.com/feed",
    "arXiv cs.AI":             "http://arxiv.org/rss/cs.AI",
    "arXiv cs.LG":             "http://arxiv.org/rss/cs.LG",
}

HN_TOP_URL      = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL     = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HN_MIN_POINTS   = 100
HN_KEYWORDS     = {"ai", "automation", "llm", "agent", "ml", "machine learning"}

REDDIT_SUBS     = ["MachineLearning", "ChatGPT", "LocalLLaMA", "automation"]
REDDIT_MIN_UPS  = 50

SEEN_STORIES_PATH = Path("seen_stories.json")
SEEN_WINDOW_HOURS = 72

BATCH_SIZE = 10  # stories per Claude scoring call

SCORING_PROMPT = """You are scoring AI/tech news stories for CSK Tech Solutions' social media pipeline.
CSK's audience is: accounting firms (10-50 staff), insurance agencies, marketing agencies, and funded startups (Seed–Series B) in FinTech, HealthTech, SaaS, InsurTech.

Score each story on 4 criteria. Current UTC time: {now_utc}

Stories to score (JSON array):
{stories_json}

For EACH story, return a JSON object. Return ONLY a JSON array with one object per story, no extra text:
[
  {{
    "story_id": "<the story_id from input>",
    "audience_relevance": <0-3>,
    "recency": <0-2>,
    "impact_level": <0-3>,
    "uniqueness": <0-2>,
    "total": <sum>,
    "business_implications": ["<implication 1>", "<implication 2>", "<implication 3>"]
  }},
  ...
]

Scoring criteria:
audience_relevance (0-3):
  3 = directly affects accounting firms, insurance agencies, or funded startups
  2 = affects SMBs or tech teams broadly
  1 = general AI news with indirect business impact
  0 = research/academic with no near-term business relevance

recency (0-2):
  2 = published in last 4 hours relative to {now_utc}
  1 = published in last 24 hours
  0 = older than 24 hours

impact_level (0-3):
  3 = major model release, breakthrough capability, or industry-changing announcement
  2 = significant product update, research paper with real-world implications
  1 = minor update, incremental improvement
  0 = opinion piece or low-impact news

uniqueness (0-2):
  2 = not yet widely covered, fresh story
  1 = covered in 1-2 places
  0 = already everywhere, oversaturated

business_implications: exactly 3 bullet strings explaining relevance to CSK's audience segments."""


# ---------------------------------------------------------------------------
# Seen-stories deduplication
# ---------------------------------------------------------------------------

def load_seen_stories() -> dict:
    """
    Loads the seen_stories.json rolling window from project root.
    Returns a dict mapping story_id -> ISO timestamp of when it was first seen.
    Creates an empty file if it doesn't exist.
    """
    if not SEEN_STORIES_PATH.exists():
        return {}
    try:
        with open(SEEN_STORIES_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load seen_stories.json: {e}")
        return {}


def save_seen_stories(seen: dict) -> None:
    """
    Prunes entries older than 72 hours, then writes seen_stories.json
    back to project root.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SEEN_WINDOW_HOURS)
    pruned = {
        sid: ts for sid, ts in seen.items()
        if datetime.fromisoformat(ts) > cutoff
    }
    with open(SEEN_STORIES_PATH, "w") as f:
        json.dump(pruned, f, indent=2)
    logger.info(f"Saved seen_stories.json ({len(pruned)} entries after pruning)")


def _story_id(source: str, url: str) -> str:
    """Generates a stable story ID from source name + URL."""
    digest = hashlib.md5(url.encode()).hexdigest()[:8]
    slug = source.lower().replace(" ", "_")[:20]
    return f"{slug}_{digest}"


# ---------------------------------------------------------------------------
# Output directory
# ---------------------------------------------------------------------------

def _ensure_output_dir(date_str: str) -> Path:
    """Creates content/{date}/track2/ directory if it doesn't exist."""
    path = Path(f"content/{date_str}/track2")
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# RSS scrapers
# ---------------------------------------------------------------------------

def scrape_rss_feeds(seen: dict) -> list[dict]:
    """
    Iterates all RSS_FEEDS, parses each with feedparser, and extracts new stories.
    Skips any URL already in the seen_stories dict.
    Applies a 1-2 second polite delay between each feed request.
    Returns a flat list of raw story dicts.
    """
    logger.info(f"Scraping {len(RSS_FEEDS)} RSS feeds...")
    results = []
    now = datetime.now(timezone.utc)

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            new_count = 0
            for entry in feed.entries:
                url = entry.get("link", "")
                if not url:
                    continue
                sid = _story_id(source_name, url)
                if sid in seen:
                    continue

                # Parse published date
                published_at = now.isoformat()
                if entry.get("published_parsed"):
                    try:
                        published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                    except Exception:
                        pass

                # Summary: prefer summary field, fall back to description
                summary_raw = entry.get("summary") or entry.get("description") or ""
                # Strip basic HTML tags from summary
                import re
                summary = re.sub(r"<[^>]+>", "", summary_raw)[:300].strip()

                results.append({
                    "story_id": sid,
                    "source": source_name,
                    "source_url": url,
                    "title": entry.get("title", "").strip(),
                    "summary": summary,
                    "published_at": published_at,
                    "scraped_at": now.isoformat(),
                })
                seen[sid] = now.isoformat()
                new_count += 1

            logger.info(f"{source_name}: {new_count} new stories")
            polite_delay(1.0, 2.0)
        except Exception as e:
            logger.warning(f"RSS feed '{source_name}' failed: {e}")
            continue

    logger.info(f"RSS total new stories: {len(results)}")
    return results


def scrape_hacker_news_news(seen: dict) -> list[dict]:
    """
    Fetches top 100 HN stories, filters to >= 100 points with matching keywords,
    skips already-seen URLs. Returns list of raw story dicts.
    """
    logger.info("Scraping Hacker News for news...")
    results = []
    now = datetime.now(timezone.utc)
    try:
        resp = requests.get(HN_TOP_URL, timeout=10)
        resp.raise_for_status()
        story_ids = resp.json()[:100]

        for story_id in story_ids:
            try:
                item_resp = requests.get(HN_ITEM_URL.format(story_id), timeout=10)
                item_resp.raise_for_status()
                item = item_resp.json()
                if not item or item.get("type") != "story":
                    continue
                title = item.get("title", "")
                score = item.get("score", 0)
                url = item.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                if score < HN_MIN_POINTS:
                    continue
                if not any(kw in title.lower() for kw in HN_KEYWORDS):
                    continue
                sid = _story_id("hacker_news", url)
                if sid in seen:
                    continue

                results.append({
                    "story_id": sid,
                    "source": "Hacker News",
                    "source_url": url,
                    "title": title,
                    "summary": "",
                    "published_at": now.isoformat(),
                    "scraped_at": now.isoformat(),
                    "hn_score": score,
                    "hn_comments": item.get("descendants", 0),
                })
                seen[sid] = now.isoformat()
                api_delay()
            except Exception as e:
                logger.warning(f"HN item {story_id} failed: {e}")
                continue
    except Exception as e:
        logger.error(f"Hacker News news scraper failed: {e}")

    logger.info(f"Hacker News: {len(results)} new stories")
    return results


def scrape_reddit_news(seen: dict) -> list[dict]:
    """
    Pulls hot posts from AI/automation subreddits using Reddit's public JSON
    endpoint (no credentials required).
    Filters to posts from the last 4 hours with >= 50 upvotes.
    Skips already-seen URLs. Returns list of raw story dicts.
    """
    logger.info("Scraping Reddit for news...")
    results = []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=4)
    headers = {"User-Agent": "CSKContentBot/1.0"}

    for sub_name in REDDIT_SUBS:
        try:
            url = f"https://www.reddit.com/r/{sub_name}/hot.json?limit=25"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            for child in posts:
                post = child.get("data", {})
                if post.get("score", 0) < REDDIT_MIN_UPS:
                    continue
                created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
                if created < cutoff:
                    continue
                post_url = f"https://reddit.com{post.get('permalink', '')}"
                sid = _story_id(f"reddit_{sub_name}", post_url)
                if sid in seen:
                    continue
                results.append({
                    "story_id": sid,
                    "source": f"Reddit r/{sub_name}",
                    "source_url": post_url,
                    "title": post.get("title", ""),
                    "summary": (post.get("selftext") or "")[:300],
                    "published_at": created.isoformat(),
                    "scraped_at": now.isoformat(),
                    "reddit_score": post.get("score", 0),
                    "reddit_comments": post.get("num_comments", 0),
                })
                seen[sid] = now.isoformat()
            polite_delay()
        except Exception as e:
            logger.warning(f"Reddit r/{sub_name} failed: {e}")
            continue

    logger.info(f"Reddit: {len(results)} new stories")
    return results


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _chunk(lst: list, size: int) -> list[list]:
    """Splits a list into chunks of at most `size` items."""
    return [lst[i:i + size] for i in range(0, len(lst), size)]


def score_stories_batch(stories: list[dict], client: Anthropic) -> dict:
    """
    Sends a batch of up to BATCH_SIZE stories to Claude for scoring in a single API call.
    Returns a dict mapping story_id -> score dict.
    Falls back to zero scores for any story that fails to parse.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stories_input = [
        {"story_id": s["story_id"], "title": s["title"], "summary": s["summary"],
         "published_at": s.get("published_at", "")}
        for s in stories
    ]
    prompt = SCORING_PROMPT.format(
        now_utc=now_utc,
        stories_json=json.dumps(stories_input, indent=2),
    )

    fallback = {
        s["story_id"]: {
            "audience_relevance": 0, "recency": 0, "impact_level": 0,
            "uniqueness": 0, "total": 0, "business_implications": [],
        }
        for s in stories
    }

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300 * len(stories),
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        scored_list = json.loads(raw)
        result = {}
        for item in scored_list:
            sid = item.pop("story_id")
            result[sid] = item
        return result
    except Exception as e:
        logger.warning(f"Batch scoring failed ({len(stories)} stories): {e}")
        return fallback


def score_all_stories(stories: list[dict]) -> list[dict]:
    """
    Batches all stories into groups of BATCH_SIZE, scores each batch with Claude,
    merges scores back into story dicts, and assigns routing labels.
    Returns the full list with scores and route fields populated.
    """
    logger.info(f"Scoring {len(stories)} stories in batches of {BATCH_SIZE}...")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    all_scores: dict = {}
    for batch in _chunk(stories, BATCH_SIZE):
        batch_scores = score_stories_batch(batch, client)
        all_scores.update(batch_scores)
        api_delay()

    for story in stories:
        sid = story["story_id"]
        scores = all_scores.get(sid, {
            "audience_relevance": 0, "recency": 0, "impact_level": 0,
            "uniqueness": 0, "total": 0, "business_implications": [],
        })
        story["scores"] = {
            "audience_relevance": scores.get("audience_relevance", 0),
            "recency":            scores.get("recency", 0),
            "impact_level":       scores.get("impact_level", 0),
            "uniqueness":         scores.get("uniqueness", 0),
            "total":              scores.get("total", 0),
        }
        story["business_implications"] = scores.get("business_implications", [])
        total = story["scores"]["total"]
        if total >= 7:
            story["route"] = "auto_post"
        elif total >= 5:
            story["route"] = "slack_review"
        else:
            story["route"] = "skip"

    return stories


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_news_scraper(date_str: str) -> dict:
    """
    Scrapes all sources, deduplicates, scores stories, routes them.
    Returns: {"auto_post": [...], "slack_review": [...], "skipped": count}
    date_str format: "2026-03-29"

    Execution order:
    1. Load seen_stories.json (72-hour rolling window)
    2. Scrape RSS feeds (16 sources), Hacker News, Reddit — each in try/except
    3. Save news_items.json with all raw stories
    4. Score all stories with Claude in batches of 10
    5. Route: auto_post (>=7), slack_review (5-6), skip (<5)
    6. Save scored_news.json (score >= 5 only)
    7. Update and prune seen_stories.json
    8. Return routing buckets
    """
    logger.info(f"=== News scraper starting for {date_str} ===")
    output_dir = _ensure_output_dir(date_str)

    # 1. Load seen stories
    seen = load_seen_stories()
    logger.info(f"Loaded {len(seen)} seen story IDs from rolling window")

    # 2. Scrape all sources
    all_stories: list[dict] = []
    for name, fn, args in [
        ("RSS feeds",     scrape_rss_feeds,       [seen]),
        ("Hacker News",   scrape_hacker_news_news, [seen]),
        ("Reddit",        scrape_reddit_news,      [seen]),
    ]:
        try:
            items = fn(*args)
            all_stories.extend(items)
        except Exception as e:
            logger.error(f"{name} scraper crashed unexpectedly: {e}")

    logger.info(f"Total new stories this run: {len(all_stories)}")

    # 3. Save raw
    raw_path = output_dir / "news_items.json"
    with open(raw_path, "w") as f:
        json.dump(all_stories, f, indent=2)
    logger.info(f"Saved raw news → {raw_path}")

    if not all_stories:
        save_seen_stories(seen)
        return {"auto_post": [], "slack_review": [], "skipped": 0}

    # 4. Score
    scored = score_all_stories(all_stories)

    # 5 & 6. Route and save scored (score >= 5 only)
    auto_post    = [s for s in scored if s["route"] == "auto_post"]
    slack_review = [s for s in scored if s["route"] == "slack_review"]
    skipped      = [s for s in scored if s["route"] == "skip"]

    eligible = auto_post + slack_review
    eligible.sort(key=lambda x: x["scores"]["total"], reverse=True)

    scored_path = output_dir / "scored_news.json"
    with open(scored_path, "w") as f:
        json.dump(eligible, f, indent=2)
    logger.info(f"Saved scored news → {scored_path}")

    # 7. Persist seen stories
    save_seen_stories(seen)

    logger.info(
        f"=== News scraper done: {len(auto_post)} auto-post, "
        f"{len(slack_review)} slack review, {len(skipped)} skipped ==="
    )

    return {
        "auto_post":    auto_post,
        "slack_review": slack_review,
        "skipped":      len(skipped),
    }


if __name__ == "__main__":
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = run_news_scraper(today)
    print(f"auto_post: {len(result['auto_post'])}")
    print(f"slack_review: {len(result['slack_review'])}")
    print(f"skipped: {result['skipped']}")
