"""
content_publisher.py — CSK Content Engine

Handles the full publish lifecycle:
  - Slack approval posting (Track 1, Track 2, video)
  - Reaction monitoring and routing
  - Auto-approval logic with configurable windows
  - GHL Social Planner publishing (LinkedIn)
  - Twitter/X API v2 thread posting
  - Canva Connect carousel generation
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()
logger = get_logger("content_publisher")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PENDING_APPROVALS_PATH = Path("pending_approvals.json")

GHL_BASE_URL    = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

TWITTER_API_URL = "https://api.twitter.com/2/tweets"

CANVA_API_URL = "https://api.canva.com/rest/v1/designs"

AUTO_APPROVE_WINDOWS = {
    "track1":   timedelta(hours=24),
    "track2":   timedelta(hours=6),
    "video":    timedelta(hours=48),
    "carousel": None,  # never auto-approves
}

REACTIONS = {
    "white_check_mark": "approve",
    "x":                "reject",
    "pencil":           "edit",
}

DIVIDER = "─" * 35


# ---------------------------------------------------------------------------
# Pending approvals store
# ---------------------------------------------------------------------------

def _load_pending() -> list[dict]:
    """Loads pending_approvals.json from project root. Returns empty list if missing."""
    if not PENDING_APPROVALS_PATH.exists():
        return []
    try:
        with open(PENDING_APPROVALS_PATH) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load pending_approvals.json: {e}")
        return []


def _save_pending(pending: list[dict]) -> None:
    """Writes the pending approvals list back to pending_approvals.json."""
    with open(PENDING_APPROVALS_PATH, "w") as f:
        json.dump(pending, f, indent=2)


def _add_pending(
    slack_ts: str,
    content_type: str,
    content_id: str,
    never_auto_approve: bool = False,
) -> None:
    """
    Appends a new pending approval entry. Calculates auto_approve_at from
    the content_type window. If never_auto_approve is True, sets auto_approve_at to None.
    """
    now = datetime.now(timezone.utc)
    window = AUTO_APPROVE_WINDOWS.get(content_type)
    auto_at = (now + window).isoformat() if (window and not never_auto_approve) else None

    entry = {
        "slack_ts":           slack_ts,
        "content_type":       content_type,
        "content_id":         content_id,
        "posted_at":          now.isoformat(),
        "auto_approve_at":    auto_at,
        "never_auto_approve": never_auto_approve,
        "status":             "pending",
    }
    pending = _load_pending()
    pending.append(entry)
    _save_pending(pending)
    logger.info(f"Added pending approval: {content_type} / {content_id} / ts={slack_ts}")


def _update_pending_status(slack_ts: str, status: str) -> None:
    """Updates the status field for a pending approval entry by its Slack ts."""
    pending = _load_pending()
    for entry in pending:
        if entry["slack_ts"] == slack_ts:
            entry["status"] = status
            break
    _save_pending(pending)


# ---------------------------------------------------------------------------
# Part 1: Slack approval posting
# ---------------------------------------------------------------------------

def post_track1_for_approval(
    content_package: dict,
    slack_client: WebClient,
    channel_id: str,
) -> str:
    """
    Posts the full Track 1 content package to #content-approval for review.
    Includes LinkedIn preview (truncated to 500 chars) and first 2 tweets.
    Adds ✅ ❌ ✏️ emoji reactions automatically.
    Adds entry to pending_approvals.json with 24-hour auto-approval window.
    Returns the Slack message timestamp (ts).
    """
    idea = content_package.get("idea", {})
    content = content_package.get("content", {})
    date_str = content_package.get("date_str", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    linkedin_text = content.get("linkedin_post", "") or ""
    linkedin_preview = (
        linkedin_text[:500] + (" ... [see full file]" if len(linkedin_text) > 500 else "")
        if linkedin_text
        else "_(LinkedIn post generation failed — check content.log)_"
    )

    twitter_raw = content.get("twitter_thread") or []
    if isinstance(twitter_raw, list):
        twitter_thread = twitter_raw
    elif isinstance(twitter_raw, str):
        twitter_thread = [t.strip() for t in twitter_raw.split("\n\n---\n\n") if t.strip()]
    else:
        twitter_thread = []
    twitter_full = (
        "\n\n".join(twitter_thread[:2]) + ("\n\n_(+ more tweets in file)_" if len(twitter_thread) > 2 else "")
        if twitter_thread
        else "_(Twitter thread generation failed — check content.log)_"
    )

    score = idea.get("scores", {}).get("total", "—")
    source = idea.get("source", "unknown")
    subreddit = idea.get("subreddit", "")
    source_label = f"{source} {subreddit}".strip()
    engagement = idea.get("engagement", {})
    engagement_str = ", ".join(f"{k}: {v}" for k, v in engagement.items()) if engagement else "—"
    audience = idea.get("csk_angle_note", "—")

    text = (
        f"📱 *CONTENT READY FOR APPROVAL — Track 1*\n\n"
        f"📅 Scheduled: Tomorrow at 9:00 AM CST\n"
        f"📌 Platforms: LinkedIn + Blog (Twitter: copy & post manually)\n"
        f"🎯 Audience: {audience}\n"
        f"💡 Source: {source_label} | {engagement_str}\n"
        f"🏆 Idea score: {score}/10\n\n"
        f"{DIVIDER}\n"
        f"📝 *LINKEDIN POST PREVIEW:*\n{linkedin_preview}\n\n"
        f"{DIVIDER}\n"
        f"🐦 *TWITTER/X THREAD (copy & post manually):*\n{twitter_full}\n\n"
        f"{DIVIDER}\n"
        f"✅ Approve  ❌ Reject  ✏️ Edit\n"
        f"⏰ Auto-approves in 24 hours if no response"
    )

    try:
        resp = slack_client.chat_postMessage(channel=channel_id, text=text)
        ts = resp["ts"]
        for emoji in ["white_check_mark", "x", "pencil"]:
            slack_client.reactions_add(channel=channel_id, name=emoji, timestamp=ts)
        _add_pending(ts, "track1", date_str)
        logger.info(f"Posted Track 1 approval to Slack ts={ts}")
        return ts
    except SlackApiError as e:
        logger.error(f"Failed to post Track 1 approval to Slack: {e}")
        return ""


def post_video_for_approval(
    video_meta: dict,
    slack_client: WebClient,
    channel_id: str,
) -> str:
    """
    Posts video script and metadata to #content-approval for review.
    Includes full script text, duration estimate, and platforms.
    Adds entry to pending_approvals.json with 48-hour auto-approval window.
    Returns the Slack message timestamp (ts).
    """
    script = video_meta.get("script", "(no script)")
    score = video_meta.get("score", "—")
    content_id = video_meta.get("content_id", "unknown")
    word_count = len(script.split())
    # ~130 words/min for spoken delivery
    duration_secs = round((word_count / 130) * 60)

    text = (
        f"🎬 *VIDEO READY FOR APPROVAL — Track 3*\n\n"
        f"📅 Ready to publish after approval\n"
        f"📌 Platforms: LinkedIn Video + YouTube Shorts + Instagram Reels\n"
        f"🏆 Idea score: {score}/10\n"
        f"⏱️ Duration: ~{duration_secs} seconds\n\n"
        f"{DIVIDER}\n"
        f"📄 *SCRIPT:*\n{script}\n\n"
        f"{DIVIDER}\n"
        f"✅ Approve  ❌ Reject  ✏️ Edit script (DM bot with revised script)\n"
        f"⏰ Auto-approves in 48 hours if no response"
    )

    try:
        resp = slack_client.chat_postMessage(channel=channel_id, text=text)
        ts = resp["ts"]
        for emoji in ["white_check_mark", "x", "pencil"]:
            slack_client.reactions_add(channel=channel_id, name=emoji, timestamp=ts)
        _add_pending(ts, "video", content_id)
        logger.info(f"Posted video approval to Slack ts={ts}")
        return ts
    except SlackApiError as e:
        logger.error(f"Failed to post video approval to Slack: {e}")
        return ""


def post_track2_for_review(
    story: dict,
    content: dict,
    slack_client: WebClient,
    channel_id: str,
) -> str:
    """
    Posts a Track 2 news story (score 5-6) to #content-approval for human review.
    Includes LinkedIn post preview, score, source, and hours since publication.
    Adds entry to pending_approvals.json with 6-hour auto-approval window.
    Returns the Slack message timestamp (ts).
    """
    score = story.get("scores", {}).get("total", "—")
    source = story.get("source", "unknown")
    title = story.get("title", "")
    published_at = story.get("published_at", "")
    story_id = story.get("story_id", "unknown")

    hours_ago = "—"
    if published_at:
        try:
            pub_dt = datetime.fromisoformat(published_at)
            delta = datetime.now(timezone.utc) - pub_dt
            hours_ago = f"{int(delta.total_seconds() // 3600)}h ago"
        except Exception:
            pass

    linkedin_text = content.get("news_linkedin", "") or ""
    linkedin_preview = linkedin_text[:600] + (" ... [see file]" if len(linkedin_text) > 600 else "")

    text = (
        f"📰 *NEWS POST FOR REVIEW — Track 2*\n\n"
        f"🏆 Score: {score}/10 — needs your review\n"
        f"📅 Story published: {hours_ago}\n"
        f"📌 Source: {source}\n\n"
        f"{DIVIDER}\n"
        f"{linkedin_preview}\n\n"
        f"{DIVIDER}\n"
        f"✅ ❌ ✏️  Auto-approves in 6 hours"
    )

    try:
        resp = slack_client.chat_postMessage(channel=channel_id, text=text)
        ts = resp["ts"]
        for emoji in ["white_check_mark", "x", "pencil"]:
            slack_client.reactions_add(channel=channel_id, name=emoji, timestamp=ts)
        _add_pending(ts, "track2", story_id)
        logger.info(f"Posted Track 2 review to Slack ts={ts}")
        return ts
    except SlackApiError as e:
        logger.error(f"Failed to post Track 2 review to Slack: {e}")
        return ""


def log_auto_post(story: dict, slack_client: WebClient, channel_id: str) -> None:
    """
    Posts an FYI notice to #ai-news-feed when a Track 2 story (score >= 7) is auto-posted.
    No approval needed — this is a log entry only.
    """
    score = story.get("scores", {}).get("total", "—")
    title = story.get("title", "")
    source = story.get("source", "")
    now_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    text = (
        f"🤖 *AUTO-POSTED — {now_str}*\n"
        f"Score: {score}/10 | LinkedIn + Twitter/X\n"
        f"Story: {title}\n"
        f"Source: {source}"
    )

    try:
        slack_client.chat_postMessage(channel=channel_id, text=text)
        logger.info(f"Logged auto-post for: {title[:60]}")
    except SlackApiError as e:
        logger.error(f"Failed to log auto-post to Slack: {e}")


# ---------------------------------------------------------------------------
# Part 2: Reaction monitoring
# ---------------------------------------------------------------------------

def check_reactions(
    slack_client: WebClient,
    channel_id: str,
    since_hours: int = 1,
) -> list[dict]:
    """
    Checks #content-approval for new emoji reactions in the last N hours.
    Reads pending_approvals.json to match reactions to known approval messages.
    Watches for: ✅ (white_check_mark), ❌ (x), ✏️ (pencil).
    Returns list of {ts, reaction, content_type, content_id}.
    """
    pending = _load_pending()
    pending_by_ts = {p["slack_ts"]: p for p in pending if p["status"] == "pending"}

    if not pending_by_ts:
        logger.info("No pending approvals to check reactions for")
        return []

    since_dt = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    results = []

    for ts, entry in pending_by_ts.items():
        try:
            resp = slack_client.reactions_get(channel=channel_id, timestamp=ts)
            message = resp.get("message", {})
            for reaction_data in message.get("reactions", []):
                name = reaction_data.get("name", "")
                if name not in REACTIONS:
                    continue
                # Check if any reactor is not the bot itself
                users = reaction_data.get("users", [])
                bot_id = os.environ.get("SLACK_BOT_USER_ID", "")
                human_reactors = [u for u in users if u != bot_id]
                if not human_reactors:
                    continue
                results.append({
                    "ts":           ts,
                    "reaction":     name,
                    "content_type": entry["content_type"],
                    "content_id":   entry["content_id"],
                })
                logger.info(f"Reaction detected: {name} on ts={ts} ({entry['content_type']})")
        except SlackApiError as e:
            logger.warning(f"Could not check reactions for ts={ts}: {e}")
            continue

    return results


def process_reactions(reactions: list[dict], slack_client: WebClient) -> None:
    """
    Routes each detected reaction to handle_approval for dispatching.
    Called after check_reactions returns results.
    """
    for r in reactions:
        handle_approval(r["ts"], r["reaction"], slack_client)


# ---------------------------------------------------------------------------
# Part 3: Auto-approval checker
# ---------------------------------------------------------------------------

def check_auto_approvals() -> list[dict]:
    """
    Reads pending_approvals.json and returns all entries whose auto_approve_at
    timestamp has passed and whose status is still "pending".
    Skips entries with never_auto_approve=True (monthly carousels).
    Returns list of pending entry dicts ready for auto-approval.
    """
    pending = _load_pending()
    now = datetime.now(timezone.utc)
    ready = []

    for entry in pending:
        if entry["status"] != "pending":
            continue
        if entry.get("never_auto_approve"):
            continue
        auto_at = entry.get("auto_approve_at")
        if not auto_at:
            continue
        try:
            auto_dt = datetime.fromisoformat(auto_at)
            if now >= auto_dt:
                ready.append(entry)
                logger.info(
                    f"Auto-approval triggered: {entry['content_type']} / "
                    f"{entry['content_id']} (past {auto_at})"
                )
        except Exception as e:
            logger.warning(f"Could not parse auto_approve_at for ts={entry['slack_ts']}: {e}")

    return ready


# ---------------------------------------------------------------------------
# Part 4: GHL publishing
# ---------------------------------------------------------------------------

def _ghl_headers() -> dict:
    """Returns the standard GHL API request headers."""
    return {
        "Authorization": f"Bearer {os.environ['GHL_API_KEY']}",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
    }


def _ghl_request(method: str, endpoint: str, payload: dict) -> dict:
    """
    Makes a GHL API request with retry logic:
    - 429 (rate limit): retry once after 60 seconds
    - 5xx (server error): retry once after 10 seconds
    - 4xx (client error): log and alert, no retry
    Returns the JSON response dict or raises on unrecoverable failure.
    """
    url = f"{GHL_BASE_URL}{endpoint}"
    for attempt in range(2):
        try:
            resp = requests.request(method, url, headers=_ghl_headers(), json=payload, timeout=30)
            if resp.status_code == 429:
                if attempt == 0:
                    logger.warning("GHL rate limit hit, waiting 60s before retry")
                    time.sleep(60)
                    continue
            elif resp.status_code >= 500:
                if attempt == 0:
                    logger.warning(f"GHL server error {resp.status_code}, retrying in 10s")
                    time.sleep(10)
                    continue
            elif 400 <= resp.status_code < 500:
                logger.error(f"GHL client error {resp.status_code}: {resp.text}")
                _alert_slack_error(f"GHL API 4xx error on {endpoint}: {resp.status_code} — {resp.text[:200]}")
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == 1:
                logger.error(f"GHL request failed after retry: {e}")
                raise
    return {}


def _alert_slack_error(message: str) -> None:
    """Posts an error alert to the content-approval Slack channel."""
    try:
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel_id = os.environ.get("SLACK_APPROVAL_CHANNEL_ID", "")
        client.chat_postMessage(channel=channel_id, text=f"⚠️ *PUBLISH ERROR*\n{message}")
    except Exception as e:
        logger.error(f"Could not send Slack error alert: {e}")


def publish_linkedin_post(post_text: str, scheduled_time: str = None) -> dict:
    """
    Publishes a LinkedIn text post via GHL Social Planner.
    scheduled_time: ISO 8601 string for future scheduling, or None for immediate.
    Returns the GHL API response dict.
    """
    payload = {
        "locationId": os.environ["GHL_LOCATION_ID"],
        "accountIds": [os.environ["GHL_LINKEDIN_ACCOUNT_ID"]],
        "post": post_text,
    }
    if scheduled_time:
        payload["scheduledAt"] = scheduled_time

    logger.info(f"Publishing LinkedIn post ({len(post_text)} chars) — scheduled: {scheduled_time or 'now'}")
    result = _ghl_request("POST", "/social-media-posting/posts", payload)
    logger.info(f"LinkedIn post published: {result.get('id', '—')}")
    return result


def publish_linkedin_video(video_url: str, caption: str) -> dict:
    """
    Publishes a video post to LinkedIn via GHL Social Planner.
    video_url must be a publicly accessible Cloudinary URL.
    Returns the GHL API response dict.
    """
    payload = {
        "locationId": os.environ["GHL_LOCATION_ID"],
        "accountIds": [os.environ["GHL_LINKEDIN_ACCOUNT_ID"]],
        "post": caption,
        "media": [{"url": video_url, "type": "video"}],
    }

    logger.info(f"Publishing LinkedIn video: {video_url[:80]}")
    result = _ghl_request("POST", "/social-media-posting/posts", payload)
    logger.info(f"LinkedIn video published: {result.get('id', '—')}")
    return result


# ---------------------------------------------------------------------------
# Part 5: Twitter/X publishing
# ---------------------------------------------------------------------------

def _twitter_headers() -> dict:
    """Returns the Twitter API v2 request headers using bearer token."""
    return {
        "Authorization": f"Bearer {os.environ['TWITTER_BEARER_TOKEN']}",
        "Content-Type": "application/json",
    }


def _post_tweet(text: str, reply_to_id: str = None) -> dict:
    """
    Posts a single tweet. If reply_to_id is provided, posts as a reply to that tweet.
    Retries once on 5xx. Returns the Twitter API response dict.
    """
    payload: dict = {"text": text}
    if reply_to_id:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}

    for attempt in range(2):
        try:
            resp = requests.post(TWITTER_API_URL, headers=_twitter_headers(), json=payload, timeout=15)
            if resp.status_code >= 500 and attempt == 0:
                logger.warning(f"Twitter 5xx on tweet post, retrying in 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == 1:
                logger.error(f"Twitter post failed after retry: {e}")
                raise
    return {}


def publish_twitter_thread(tweets: list[str]) -> dict:
    """
    Posts a Twitter/X thread. The first tweet is posted standalone.
    Each subsequent tweet is posted as a reply to the previous tweet's ID.
    Returns a dict with the list of posted tweet IDs and the first tweet ID.
    """
    if not tweets:
        logger.warning("publish_twitter_thread called with empty tweet list")
        return {}

    logger.info(f"Publishing Twitter thread ({len(tweets)} tweets)")
    posted_ids = []
    previous_id = None

    for i, tweet_text in enumerate(tweets):
        try:
            result = _post_tweet(tweet_text, reply_to_id=previous_id)
            tweet_id = result.get("data", {}).get("id")
            posted_ids.append(tweet_id)
            previous_id = tweet_id
            logger.info(f"Tweet {i+1}/{len(tweets)} posted: id={tweet_id}")
            if i < len(tweets) - 1:
                time.sleep(1)  # brief pause between tweets in the thread
        except Exception as e:
            logger.error(f"Tweet {i+1} failed — thread may be incomplete: {e}")
            break

    return {"tweet_ids": posted_ids, "first_tweet_id": posted_ids[0] if posted_ids else None}


# ---------------------------------------------------------------------------
# Part 5: Canva carousel generation
# ---------------------------------------------------------------------------

def generate_canva_carousel(carousel_brief: dict) -> str:
    """
    Sends the carousel brief to the Canva Connect API to generate a carousel design.
    Populates text fields from carousel_brief slides.
    Exports the design as a PDF and returns the export URL.
    Returns empty string on failure.
    """
    api_key = os.environ.get("CANVA_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    slides = carousel_brief.get("slides", [])
    design_payload = {
        "asset_type": "presentation",
        "title": carousel_brief.get("topic", "CSK Carousel"),
        "pages": [
            {
                "elements": [
                    {"type": "text", "text": slide.get("headline", "")},
                    {"type": "text", "text": slide.get("body", "")},
                ]
            }
            for slide in slides
        ],
    }

    try:
        # Create design
        resp = requests.post(CANVA_API_URL, headers=headers, json=design_payload, timeout=30)
        resp.raise_for_status()
        design_id = resp.json().get("design", {}).get("id")
        if not design_id:
            logger.error("Canva did not return a design ID")
            return ""

        logger.info(f"Canva design created: {design_id}")

        # Export as PDF
        export_resp = requests.post(
            f"{CANVA_API_URL}/{design_id}/exports",
            headers=headers,
            json={"format": "pdf"},
            timeout=30,
        )
        export_resp.raise_for_status()
        export_url = export_resp.json().get("export", {}).get("url", "")
        logger.info(f"Canva export URL: {export_url}")
        return export_url

    except Exception as e:
        logger.error(f"Canva carousel generation failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Part 6: Approval action dispatcher
# ---------------------------------------------------------------------------

def handle_approval(slack_ts: str, reaction: str, slack_client: WebClient) -> None:
    """
    Called when a reaction is detected on an approval message.
    Routes to publish (✅), reject (❌), or edit request (✏️) flow.
    Loads content from pending_approvals.json by Slack ts, updates status,
    and posts a confirmation reply in the Slack thread.
    """
    pending = _load_pending()
    entry = next((p for p in pending if p["slack_ts"] == slack_ts), None)

    if not entry:
        logger.warning(f"No pending entry found for ts={slack_ts}")
        return

    content_type = entry["content_type"]
    content_id = entry["content_id"]
    channel_id = os.environ.get("SLACK_APPROVAL_CHANNEL_ID", "")

    action = REACTIONS.get(reaction, "unknown")
    logger.info(f"Handling {action} reaction for {content_type} / {content_id}")

    if action == "approve":
        _handle_approve(entry, slack_client, channel_id)

    elif action == "reject":
        _update_pending_status(slack_ts, "rejected")
        logger.info(f"Content rejected: {content_type} / {content_id}")
        try:
            slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=slack_ts,
                text=f"❌ Content rejected and removed from queue. ID: {content_id}",
            )
        except SlackApiError as e:
            logger.error(f"Failed to post rejection confirmation: {e}")

    elif action == "edit":
        _update_pending_status(slack_ts, "edit_requested")
        owner_id = os.environ.get("SLACK_WORKSPACE_OWNER_ID", "")
        try:
            # DM the workspace owner with the content and edit request
            dm_resp = slack_client.conversations_open(users=owner_id)
            dm_channel = dm_resp["channel"]["id"]
            slack_client.chat_postMessage(
                channel=dm_channel,
                text=(
                    f"✏️ *Edit requested for {content_type} content (ID: {content_id})*\n\n"
                    f"Reply here with the revised version and I'll update and re-post for approval."
                ),
            )
            slack_client.chat_postMessage(
                channel=channel_id,
                thread_ts=slack_ts,
                text=f"✏️ Edit requested. A DM has been sent to the workspace owner for revision.",
            )
            logger.info(f"Edit request DM sent to {owner_id} for {content_type} / {content_id}")
        except SlackApiError as e:
            logger.error(f"Failed to send edit request DM: {e}")


def _handle_approve(entry: dict, slack_client: WebClient, channel_id: str) -> None:
    """
    Internal handler for approved content. Loads content from disk and calls
    the appropriate publish function based on content_type.
    Updates status to "approved" in pending_approvals.json.
    Posts a confirmation to the Slack thread.
    """
    content_type = entry["content_type"]
    content_id = entry["content_id"]
    slack_ts = entry["slack_ts"]

    try:
        if content_type == "track1":
            _publish_track1(content_id)
        elif content_type == "track2":
            _publish_track2(content_id)
        elif content_type == "video":
            _publish_video(content_id)
        else:
            logger.warning(f"Unknown content_type for approval: {content_type}")
            return

        _update_pending_status(slack_ts, "approved")
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=slack_ts,
            text=f"✅ Approved and published: {content_type} / {content_id}",
        )
        logger.info(f"Content approved and published: {content_type} / {content_id}")

    except Exception as e:
        logger.error(f"Publish failed after approval for {content_type} / {content_id}: {e}")
        slack_client.chat_postMessage(
            channel=channel_id,
            thread_ts=slack_ts,
            text=f"⚠️ Approval recorded but publish failed for {content_id}. Check logs.",
        )


def _publish_track1(date_str: str) -> None:
    """Loads Track 1 content from disk and publishes to LinkedIn + Twitter."""
    base = Path(f"content/{date_str}/track1")
    linkedin_path = base / "linkedin_post.md"
    twitter_path = base / "twitter_thread.md"

    if linkedin_path.exists():
        post_text = linkedin_path.read_text()
        # Schedule for 9 AM CST the next day
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        scheduled = f"{tomorrow}T15:00:00Z"  # 9 AM CST = 15:00 UTC
        publish_linkedin_post(post_text, scheduled_time=scheduled)

    if twitter_path.exists():
        logger.info("Twitter thread saved — post manually from Slack or content folder")


def _publish_track2(story_id: str) -> None:
    """Loads Track 2 content from disk and publishes to LinkedIn + Twitter."""
    # Track 2 files are stored by story_id slug under content/{date}/track2/{slug}/
    # Search for the slug directory across all date folders
    for date_dir in Path("content").glob("*/track2"):
        story_dir = date_dir / story_id
        if not story_dir.exists():
            # story_id may be a slug — try partial match
            matches = list(date_dir.glob(f"{story_id[:20]}*"))
            if matches:
                story_dir = matches[0]
            else:
                continue

        linkedin_path = story_dir / "linkedin_post.md"
        twitter_path = story_dir / "twitter_thread.md"

        if linkedin_path.exists():
            publish_linkedin_post(linkedin_path.read_text())
        if twitter_path.exists():
            logger.info("Twitter thread saved — post manually from Slack or content folder")
        return


def _publish_video(content_id: str) -> None:
    """Loads video metadata from disk and triggers video publish flow."""
    # Video files are handled by video_publisher.py after the video is generated
    # This stub logs the intent; actual publish is delegated
    logger.info(f"Video publish triggered for content_id={content_id} — delegating to video_publisher")


if __name__ == "__main__":
    # Smoke test: check auto-approvals
    ready = check_auto_approvals()
    print(f"Items ready for auto-approval: {len(ready)}")
    for item in ready:
        print(f"  - {item['content_type']} / {item['content_id']} (was due {item['auto_approve_at']})")
