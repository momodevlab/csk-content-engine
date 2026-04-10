"""
idea_scraper.py — CSK Content Engine

Scrapes 6 sources for content ideas (Reddit, Hacker News, Google Trends,
YouTube, Twitter/X, Quora), scores each idea 1-10 using Claude, and returns
the top 3 ideas of the day for Track 1 LinkedIn content creation.
"""

import os
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import requests
import feedparser
from pytrends.request import TrendReq
from googleapiclient.discovery import build
from playwright.sync_api import sync_playwright
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.logger import get_logger
from utils.rate_limiter import polite_delay, api_delay

load_dotenv()
logger = get_logger("idea_scraper")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REDDIT_SUBREDDITS = [
    "automation", "artificial", "MachineLearning", "datascience",
    "accounting", "smallbusiness", "insurance", "marketing",
    "Entrepreneur", "startups",
]
REDDIT_MIN_UPVOTES = 50

HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
HN_MIN_POINTS = 100
HN_KEYWORDS = {"ai", "automation", "data", "saas", "accounting", "workflow", "llm", "agent"}

GTRENDS_KEYWORDS = [
    "AI automation",
    "data migration",
    "workflow automation",
    "accounting software",
    "business intelligence",
]

YOUTUBE_QUERIES = [
    "accounting automation 2026",
    "AI for small business",
    "workflow automation tutorial",
    "AI agents business",
]
YOUTUBE_MIN_VIEWS = 1000

TWITTER_QUERY = "#automation OR #AItools OR #accountingtech OR #insurtech lang:en -is:retweet"
TWITTER_MIN_LIKES = 50

QUORA_QUERIES = [
    "automate accounting workflows",
    "AI tools for insurance agency",
    "data migration small business",
]

SCORING_PROMPT = """You are scoring a social media post or article for relevance to CSK Tech Solutions.
CSK sells AI, automation, and data engineering services to:
- Accounting and bookkeeping firms (10–50 staff)
- Insurance agencies
- Marketing agencies
- Funded startups (Seed–Series B) in FinTech, HealthTech, SaaS, InsurTech

Score this item on 4 criteria and return ONLY valid JSON with no extra text:

Item title: {title}
Item body/description: {body}
Source: {source}

Scoring criteria:
1. audience_relevance (0-3): Does this directly affect our ICP?
   3 = core pain point for accounting/insurance/marketing/startup
   2 = relevant to adjacent audience
   1 = loosely related
   0 = not relevant

2. engagement_signal (0-3): How much traction did this get?
   3 = viral (1000+ upvotes/likes or 100k+ views)
   2 = strong (200-999 upvotes or 10k-99k views)
   1 = moderate (50-199 upvotes or 1k-9k views)
   0 = weak
   (Use provided engagement numbers: {engagement_summary})

3. csk_angle (0-2): Can we tie this to a CSK service?
   2 = directly maps to AI/ML, automation, data migration, or system integration
   1 = loosely connected
   0 = no clear tie-in

4. originality (0-2): Is this already oversaturated on LinkedIn?
   2 = fresh angle or emerging topic
   1 = common but we can add unique perspective
   0 = overdone

Return exactly this JSON structure:
{{
  "audience_relevance": <int>,
  "engagement_signal": <int>,
  "csk_angle": <int>,
  "originality": <int>,
  "total": <sum of all four>,
  "csk_angle_note": "<one sentence explaining the CSK tie-in>",
  "content_hook": "<one punchy LinkedIn hook sentence based on this topic>"
}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_id(source: str, text: str) -> str:
    """Generate a short stable ID from source + title."""
    digest = hashlib.md5(text.encode()).hexdigest()[:8]
    return f"{source}_{digest}"


def _deduplicate(items: list[dict]) -> list[dict]:
    """Remove items with identical or near-identical titles."""
    seen_titles = set()
    unique = []
    for item in items:
        key = item["title"].lower().strip()[:80]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(item)
    return unique


def _ensure_output_dir(date_str: str) -> Path:
    """Create content/{date}/ideas/ directory if it doesn't exist."""
    path = Path(f"content/{date_str}/ideas")
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_reddit(date_str: str) -> list[dict]:
    """
    Scrapes top posts from last 24 hours across subreddits using Reddit's
    public JSON endpoint (no credentials required).
    Filters to posts with >= 50 upvotes. Returns list of idea dicts.
    """
    logger.info("Scraping Reddit...")
    results = []
    headers = {"User-Agent": "CSKContentBot/1.0"}
    for sub_name in REDDIT_SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{sub_name}/top.json?t=day&limit=10"
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            for child in posts:
                post = child.get("data", {})
                if post.get("score", 0) < REDDIT_MIN_UPVOTES:
                    continue
                results.append({
                    "id": _make_id("reddit", post.get("title", "")),
                    "source": "reddit",
                    "subreddit": f"r/{sub_name}",
                    "title": post.get("title", ""),
                    "body_preview": (post.get("selftext") or "")[:500],
                    "url": f"https://reddit.com{post.get('permalink', '')}",
                    "engagement": {"score": post.get("score", 0), "comments": post.get("num_comments", 0)},
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
            polite_delay()
        except Exception as e:
            logger.warning(f"Reddit r/{sub_name} failed: {e}")
            continue
    logger.info(f"Reddit: {len(results)} items above threshold")
    return results


def scrape_hacker_news() -> list[dict]:
    """
    Fetches top 100 HN stories via the public Firebase API.
    Filters to stories with >= 100 points whose title contains relevant keywords.
    Returns list of idea dicts.
    """
    logger.info("Scraping Hacker News...")
    results = []
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
                if score < HN_MIN_POINTS:
                    continue
                if not any(kw in title.lower() for kw in HN_KEYWORDS):
                    continue
                results.append({
                    "id": _make_id("hn", title),
                    "source": "hacker_news",
                    "title": title,
                    "body_preview": "",
                    "url": item.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "engagement": {"score": score, "comments": item.get("descendants", 0)},
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
                api_delay()
            except Exception as e:
                logger.warning(f"HN item {story_id} failed: {e}")
                continue
    except Exception as e:
        logger.error(f"Hacker News scraper failed: {e}")
    logger.info(f"Hacker News: {len(results)} items above threshold")
    return results


def scrape_google_trends() -> list[dict]:
    """
    Pulls rising breakout terms from Google Trends for 5 seed keywords.
    Returns list of idea dicts, one per rising term with relative interest score.
    """
    logger.info("Scraping Google Trends...")
    results = []
    try:
        pytrends = TrendReq(hl="en-US", tz=360)
        pytrends.build_payload(GTRENDS_KEYWORDS, timeframe="now 7-d", geo="US")
        related = pytrends.related_queries()
        for keyword, data in related.items():
            if data.get("rising") is None:
                continue
            rising_df = data["rising"]
            if rising_df is None or rising_df.empty:
                continue
            for _, row in rising_df.head(3).iterrows():
                query = row.get("query", "")
                value = row.get("value", 0)
                if not query:
                    continue
                results.append({
                    "id": _make_id("gtrends", query),
                    "source": "google_trends",
                    "seed_keyword": keyword,
                    "title": f"Rising search: {query}",
                    "body_preview": f"Relative interest score: {value}. Rising breakout term related to '{keyword}'.",
                    "url": f"https://trends.google.com/trends/explore?q={query.replace(' ', '+')}&geo=US",
                    "engagement": {"score": int(value), "comments": 0},
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        polite_delay()
    except Exception as e:
        logger.error(f"Google Trends scraper failed: {e}")
    logger.info(f"Google Trends: {len(results)} rising terms found")
    return results


def scrape_youtube() -> list[dict]:
    """
    Searches YouTube Data API v3 for 4 queries filtered to last 30 days.
    Filters to videos with >= 1000 views. Returns list of idea dicts.
    """
    logger.info("Scraping YouTube...")
    results = []
    try:
        youtube = build("youtube", "v3", developerKey=os.environ["YOUTUBE_API_KEY"])
        published_after = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00Z")

        for query in YOUTUBE_QUERIES:
            try:
                search_resp = youtube.search().list(
                    q=query,
                    part="id,snippet",
                    type="video",
                    publishedAfter=published_after,
                    maxResults=10,
                    order="viewCount",
                ).execute()

                video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
                if not video_ids:
                    continue

                stats_resp = youtube.videos().list(
                    part="statistics,snippet",
                    id=",".join(video_ids),
                ).execute()

                for video in stats_resp.get("items", []):
                    stats = video.get("statistics", {})
                    view_count = int(stats.get("viewCount", 0))
                    if view_count < YOUTUBE_MIN_VIEWS:
                        continue
                    snippet = video.get("snippet", {})
                    title = snippet.get("title", "")
                    description = snippet.get("description", "")[:300]
                    results.append({
                        "id": _make_id("youtube", title),
                        "source": "youtube",
                        "search_query": query,
                        "title": title,
                        "body_preview": description,
                        "url": f"https://www.youtube.com/watch?v={video['id']}",
                        "engagement": {
                            "score": view_count,
                            "comments": int(stats.get("commentCount", 0)),
                        },
                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                    })
                api_delay()
            except Exception as e:
                logger.warning(f"YouTube query '{query}' failed: {e}")
                continue
    except Exception as e:
        logger.error(f"YouTube scraper failed: {e}")
    logger.info(f"YouTube: {len(results)} videos above threshold")
    return results


def scrape_twitter() -> list[dict]:
    """
    Searches Twitter/X recent tweets using bearer token (read-only).
    Filters to tweets with >= 50 likes from the last 24 hours.
    Returns list of idea dicts. Skips silently if no bearer token is set.
    """
    logger.info("Scraping Twitter/X...")
    results = []
    try:
        bearer_token = os.environ.get("TWITTER_BEARER_TOKEN", "")
        if not bearer_token:
            logger.info("Twitter/X: no bearer token set, skipping")
            return results
        headers = {"Authorization": f"Bearer {bearer_token}"}
        params = {
            "query": TWITTER_QUERY,
            "max_results": 100,
            "tweet.fields": "public_metrics,created_at,author_id",
            "expansions": "author_id",
        }
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for tweet in data.get("data", []):
            metrics = tweet.get("public_metrics", {})
            likes = metrics.get("like_count", 0)
            if likes < TWITTER_MIN_LIKES:
                continue
            text = tweet.get("text", "")
            results.append({
                "id": _make_id("twitter", text),
                "source": "twitter",
                "title": text[:120],
                "body_preview": text,
                "url": f"https://twitter.com/i/web/status/{tweet['id']}",
                "engagement": {
                    "score": likes,
                    "comments": metrics.get("reply_count", 0),
                    "retweets": metrics.get("retweet_count", 0),
                },
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        api_delay()
    except Exception as e:
        logger.error(f"Twitter/X scraper failed: {e}")
    logger.info(f"Twitter/X: {len(results)} tweets above threshold")
    return results


def scrape_quora() -> list[dict]:
    """
    Uses Playwright headless browser to search Quora for 3 queries.
    Extracts top 3 questions per query. Uses polite 3-4 second delays
    between every request to avoid blocks. Returns list of idea dicts.
    """
    logger.info("Scraping Quora via Playwright...")
    results = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            for query in QUORA_QUERIES:
                try:
                    search_url = f"https://www.quora.com/search?q={query.replace(' ', '+')}&type=question"
                    page.goto(search_url, timeout=20000)
                    page.wait_for_timeout(3000)

                    question_elements = page.query_selector_all("a.q-box.qu-cursor--pointer")
                    count = 0
                    for el in question_elements[:3]:
                        title = el.inner_text().strip()
                        href = el.get_attribute("href") or ""
                        if not title:
                            continue
                        results.append({
                            "id": _make_id("quora", title),
                            "source": "quora",
                            "search_query": query,
                            "title": title,
                            "body_preview": f"Quora question related to: {query}",
                            "url": f"https://www.quora.com{href}" if href.startswith("/") else href,
                            "engagement": {"score": 0, "comments": 0},
                            "scraped_at": datetime.now(timezone.utc).isoformat(),
                        })
                        count += 1
                    logger.info(f"Quora '{query}': {count} questions found")
                    polite_delay(3.0, 4.0)
                except Exception as e:
                    logger.warning(f"Quora query '{query}' failed: {e}")
                    continue
            browser.close()
    except Exception as e:
        logger.error(f"Quora scraper failed: {e}")
    logger.info(f"Quora: {len(results)} questions total")
    return results


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_idea(item: dict, client: Anthropic) -> dict:
    """
    Sends a single idea to Claude for scoring against CSK's 4 criteria.
    Returns the item dict with a 'scores' key added.
    Falls back to a zero score if the API call fails.
    """
    engagement = item.get("engagement", {})
    engagement_summary = ", ".join(f"{k}: {v}" for k, v in engagement.items())

    prompt = SCORING_PROMPT.format(
        title=item.get("title", ""),
        body=item.get("body_preview", ""),
        source=item.get("source", ""),
        engagement_summary=engagement_summary,
    )

    try:
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        scores = json.loads(raw)
        item["scores"] = scores
        item["csk_angle_note"] = scores.pop("csk_angle_note", "")
        item["content_hook"] = scores.pop("content_hook", "")
        api_delay()
    except Exception as e:
        logger.warning(f"Scoring failed for '{item.get('title', '')[:60]}': {e}")
        item["scores"] = {
            "audience_relevance": 0,
            "engagement_signal": 0,
            "csk_angle": 0,
            "originality": 0,
            "total": 0,
        }
        item["csk_angle_note"] = ""
        item["content_hook"] = ""

    return item


def score_all_ideas(items: list[dict]) -> list[dict]:
    """
    Initializes Anthropic client and scores every item in the list.
    Returns items sorted by total score descending.
    """
    logger.info(f"Scoring {len(items)} ideas with Claude...")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    scored = [score_idea(item, client) for item in items]
    scored.sort(key=lambda x: x.get("scores", {}).get("total", 0), reverse=True)
    return scored


STYLE_ANALYSIS_PROMPT = """Analyze the writing structure and style of this high-engagement post.
Your job is to extract the structural blueprint so a different writer can mirror the same flow
and rhythm — NOT the topic or specific words.

Post title: {title}
Post body: {body}
Source: {source}
Engagement: {engagement_summary}

Return ONLY valid JSON with no extra text:
{{
  "hook_type": "<one of: shocking_stat | bold_claim | counterintuitive_truth | direct_question | short_story | number_list_opener | pain_point>",
  "hook_notes": "<1 sentence describing exactly how the hook is constructed — e.g. 'Opens with a specific dollar figure loss, then immediately pivots to a common mistake'>",
  "paragraph_rhythm": "<one of: short_punchy (1-2 sentences each) | medium_mix (2-4 sentences, varied) | long_form (dense paragraphs)>",
  "body_structure": "<describe the flow in 1-2 sentences — e.g. 'States the problem, gives 3 numbered reasons why it happens, then flips to the solution'>",
  "transition_style": "<how the post moves from hook to body to CTA — e.g. 'Uses a line break + rhetorical question to pivot between each section'>",
  "cta_style": "<one of: soft_question | direct_ask | social_proof_ask | challenge | resource_offer>",
  "cta_notes": "<1 sentence describing the exact CTA construction>",
  "emotional_trigger": "<one of: fear_of_missing_out | frustration | curiosity | aspiration | validation | urgency>",
  "formatting_notes": "<line breaks, whitespace, use of bullets/numbers, capitalization patterns — describe specifically>"
}}"""


def analyze_viral_style(items: list[dict], client: Anthropic) -> list[dict]:
    """
    Takes the top-scored items that have body text and extracts their structural
    writing patterns using Claude. Adds 'viral_style_patterns' key to each item.
    Items without body text are skipped (patterns key set to None).
    Only analyzes items with engagement_signal >= 2 to focus on genuinely viral content.
    """
    logger.info("Analyzing viral writing styles from top-scored items...")

    for item in items:
        body = item.get("body_preview", "") or item.get("summary", "")
        engagement_signal = item.get("scores", {}).get("engagement_signal", 0)

        # Only analyze items with real body text and meaningful engagement
        if not body or len(body) < 100 or engagement_signal < 2:
            item["viral_style_patterns"] = None
            continue

        engagement = item.get("engagement", {})
        engagement_summary = ", ".join(f"{k}: {v}" for k, v in engagement.items())

        prompt = STYLE_ANALYSIS_PROMPT.format(
            title=item.get("title", ""),
            body=body[:800],
            source=item.get("source", ""),
            engagement_summary=engagement_summary,
        )

        try:
            message = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            patterns = json.loads(raw)
            item["viral_style_patterns"] = patterns
            logger.info(f"Style extracted for '{item['title'][:60]}': {patterns.get('hook_type')} hook, {patterns.get('paragraph_rhythm')} rhythm")
            api_delay()
        except Exception as e:
            logger.warning(f"Style analysis failed for '{item.get('title', '')[:60]}': {e}")
            item["viral_style_patterns"] = None

    return items


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_idea_scraper(date_str: str) -> list[dict]:
    """
    Runs all scrapers, scores results, saves files, returns top 3 ideas.
    date_str format: "2026-03-29"

    Execution order:
    1. Run all 6 scrapers (each wrapped in try/except — one failure won't stop others)
    2. Deduplicate by title
    3. Save raw scraped_ideas.json
    4. Score all ideas with Claude
    5. Save scored_ideas.json (sorted by score descending)
    6. Return top 3

    Output files written to: content/{date_str}/ideas/
    """
    logger.info(f"=== Idea scraper starting for {date_str} ===")
    output_dir = _ensure_output_dir(date_str)

    # 1. Run all scrapers
    all_items: list[dict] = []
    scrapers = [
        ("Reddit", scrape_reddit, [date_str]),
        ("Hacker News", scrape_hacker_news, []),
        ("Google Trends", scrape_google_trends, []),
        ("YouTube", scrape_youtube, []),
        ("Twitter/X", scrape_twitter, []),
        ("Quora", scrape_quora, []),
    ]
    for name, fn, args in scrapers:
        try:
            items = fn(*args)
            all_items.extend(items)
            logger.info(f"{name}: {len(items)} items collected")
        except Exception as e:
            logger.error(f"{name} scraper crashed unexpectedly: {e}")

    logger.info(f"Total raw items: {len(all_items)}")

    # 2. Deduplicate
    all_items = _deduplicate(all_items)
    logger.info(f"After deduplication: {len(all_items)} items")

    # 3. Save raw
    raw_path = output_dir / "scraped_ideas.json"
    with open(raw_path, "w") as f:
        json.dump(all_items, f, indent=2)
    logger.info(f"Saved raw ideas → {raw_path}")

    # 4. Score
    scored_items = score_all_ideas(all_items)

    # 5. Analyze viral writing styles from top 10 (only items with body text + high engagement)
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    scored_items[:10] = analyze_viral_style(scored_items[:10], client)

    # 6. Save scored
    scored_path = output_dir / "scored_ideas.json"
    with open(scored_path, "w") as f:
        json.dump(scored_items, f, indent=2)
    logger.info(f"Saved scored ideas → {scored_path}")

    # 7. Return top 3
    top_3 = scored_items[:3]
    logger.info("=== Top 3 ideas ===")
    for i, idea in enumerate(top_3, 1):
        score = idea.get("scores", {}).get("total", 0)
        logger.info(f"  {i}. [{score}/10] {idea['title'][:80]}")

    return top_3


if __name__ == "__main__":
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    top_ideas = run_idea_scraper(today)
    print(json.dumps(top_ideas, indent=2))
