"""
video_publisher.py — CSK Content Engine

Handles post-caption video publishing to all platforms:
  1. Cloudinary — uploads video for a public HTTPS URL (required for LinkedIn + Instagram)
  2. LinkedIn Video — publishes via GHL Social Planner using Cloudinary URL
  3. YouTube Shorts — uploads directly from local file using YouTube Data API v3 + OAuth2
  4. Instagram Reels — two-step Graph API publish using Cloudinary URL

TikTok: scraping via Apify (idea_scraper.py). Manual posting by operator.

Also records publication results to published_content.json for performance tracking.
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import cloudinary
import cloudinary.uploader
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()
logger = get_logger("video_publisher")

GHL_BASE_URL    = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"
IG_GRAPH_URL    = "https://graph.facebook.com/v18.0"

PUBLISHED_CONTENT_PATH = Path("published_content.json")

IG_POLL_INTERVAL_SECS = 15
IG_POLL_TIMEOUT_SECS  = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Part 1: Cloudinary upload
# ---------------------------------------------------------------------------

def upload_to_cloudinary(video_path: str, public_id: str) -> str:
    """
    Uploads the captioned MP4 to Cloudinary using the configured credentials.
    Sets resource_type="video" and overwrites any existing file at that public_id.
    Returns the secure HTTPS URL for use with LinkedIn and Instagram APIs.
    Raises on upload failure.
    """
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
    )

    logger.info(f"Uploading to Cloudinary: {video_path} → {public_id}")
    result = cloudinary.uploader.upload(
        video_path,
        resource_type="video",
        public_id=public_id,
        overwrite=True,
    )
    url = result["secure_url"]
    logger.info(f"Cloudinary upload complete: {url}")
    return url


# ---------------------------------------------------------------------------
# Part 2: LinkedIn video via GHL
# ---------------------------------------------------------------------------

def publish_linkedin_video(video_url: str, caption: str) -> dict:
    """
    Posts a LinkedIn video via the GHL Social Planner API.
    video_url must be a publicly accessible Cloudinary HTTPS URL.
    caption is the full LinkedIn post text from content_creator.

    Retries once on 429 (waits 60s) or 5xx (waits 10s).
    Returns the GHL API response dict or raises on failure.
    """
    url = f"{GHL_BASE_URL}/social-media-posting/posts"
    headers = {
        "Authorization": f"Bearer {os.environ['GHL_API_KEY']}",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "locationId": os.environ["GHL_LOCATION_ID"],
        "accountIds": [os.environ["GHL_LINKEDIN_ACCOUNT_ID"]],
        "post":       caption,
        "media":      [{"type": "video", "url": video_url}],
    }

    logger.info(f"Publishing LinkedIn video: {video_url[:80]}")
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 429 and attempt == 0:
                logger.warning("GHL rate limit — waiting 60s")
                time.sleep(60)
                continue
            elif resp.status_code >= 500 and attempt == 0:
                logger.warning(f"GHL {resp.status_code} — retrying in 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"LinkedIn video published: {result.get('id', '—')}")
            return result
        except requests.RequestException as e:
            if attempt == 1:
                raise

    return {}


# ---------------------------------------------------------------------------
# Part 3: YouTube Shorts
# ---------------------------------------------------------------------------

def get_youtube_credentials() -> Credentials:
    """
    Builds OAuth2 credentials from the stored refresh token environment variables.
    Refreshes the access token automatically using the Google Auth library.
    Returns a valid Credentials object ready for use with the YouTube API client.

    OAuth setup notes (one-time, done outside this pipeline):
    1. Create a project at console.cloud.google.com
    2. Enable YouTube Data API v3
    3. Create OAuth 2.0 Client ID credentials (Desktop app type)
    4. Download the client_secret.json
    5. Run the OAuth flow once locally to get a refresh token:
         python -c "
         from google_auth_oauthlib.flow import InstalledAppFlow
         flow = InstalledAppFlow.from_client_secrets_file('client_secret.json',
             ['https://www.googleapis.com/auth/youtube.upload'])
         creds = flow.run_local_server(port=0)
         print('REFRESH TOKEN:', creds.refresh_token)
         "
    6. Store the refresh token as YOUTUBE_REFRESH_TOKEN in .env / GitHub Secrets
    """
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    creds.refresh(Request())
    logger.info("YouTube credentials refreshed")
    return creds


def publish_youtube_short(video_path: str, title: str, description: str) -> dict:
    """
    Uploads the captioned MP4 directly as a YouTube Short using resumable upload.
    YouTube auto-detects Shorts when the video is vertical (9:16) AND under 60 seconds.
    Videos 60-90s still upload successfully but may receive less Shorts distribution.

    Returns: {"video_id": "...", "url": "https://youtube.com/shorts/{id}"}
    Raises on upload failure.
    """
    creds = get_youtube_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    video_body = {
        "snippet": {
            "title":       title[:100],
            "description": description + "\n\n#Shorts #AI #Automation #CSKTechSolutions",
            "tags":        ["AI", "automation", "accounting", "business", "workflow", "Shorts"],
            "categoryId":  "28",  # Science & Technology
        },
        "status": {
            "privacyStatus":              "public",
            "selfDeclaredMadeForKids":    False,
            "madeForKids":                False,
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
    logger.info(f"Uploading YouTube Short: {title[:60]}")

    request = youtube.videos().insert(
        part="snippet,status",
        body=video_body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"YouTube upload progress: {int(status.progress() * 100)}%")

    video_id = response.get("id", "")
    url = f"https://youtube.com/shorts/{video_id}"
    logger.info(f"YouTube Short published: {url}")
    return {"video_id": video_id, "url": url}


# ---------------------------------------------------------------------------
# Part 4: Instagram Reels via Graph API
# ---------------------------------------------------------------------------

def publish_instagram_reel(video_url: str, caption: str) -> dict:
    """
    Publishes a video as an Instagram Reel using the Facebook Graph API v18.0.
    Two-step process:
      Step 1: Create a media container — Instagram fetches the video from Cloudinary
      Step 2: Poll container status every 15s until FINISHED (max 5 minutes)
      Step 3: Publish the container to make the Reel live

    Returns: {"media_id": "...", "status": "published"}
    Raises RuntimeError on container error or timeout.
    """
    ig_user_id   = os.environ["INSTAGRAM_USER_ID"]
    access_token = os.environ["INSTAGRAM_ACCESS_TOKEN"]

    if not access_token:
        raise RuntimeError(
            "Instagram token expired. Refresh at Meta Business Manager > Tools > Graph API Explorer. "
            "Tokens expire every 60 days."
        )

    # Step 1: Create container
    logger.info(f"Creating Instagram Reel container: {video_url[:80]}")
    container_resp = requests.post(
        f"{IG_GRAPH_URL}/{ig_user_id}/media",
        params={
            "media_type":    "REELS",
            "video_url":     video_url,
            "caption":       caption,
            "share_to_feed": "true",
            "access_token":  access_token,
        },
        timeout=30,
    )
    container_resp.raise_for_status()
    container_id = container_resp.json().get("id")
    if not container_id:
        raise RuntimeError(f"Instagram did not return a container_id: {container_resp.json()}")

    logger.info(f"Instagram container created: {container_id}")

    # Step 2: Poll status
    elapsed = 0
    while elapsed < IG_POLL_TIMEOUT_SECS:
        time.sleep(IG_POLL_INTERVAL_SECS)
        elapsed += IG_POLL_INTERVAL_SECS

        status_resp = requests.get(
            f"{IG_GRAPH_URL}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=15,
        )
        status_resp.raise_for_status()
        status_code = status_resp.json().get("status_code", "")
        logger.info(f"Instagram container status: {status_code} (elapsed: {elapsed}s)")

        if status_code == "FINISHED":
            break
        elif status_code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram container failed with status: {status_code}")
        # IN_PROGRESS or PUBLISHED — keep polling

    else:
        raise RuntimeError(
            f"Instagram container {container_id} did not finish within "
            f"{IG_POLL_TIMEOUT_SECS // 60} minutes"
        )

    # Step 3: Publish
    logger.info(f"Publishing Instagram Reel: container={container_id}")
    publish_resp = requests.post(
        f"{IG_GRAPH_URL}/{ig_user_id}/media_publish",
        params={
            "creation_id":  container_id,
            "access_token": access_token,
        },
        timeout=30,
    )
    publish_resp.raise_for_status()
    media_id = publish_resp.json().get("id", "")
    logger.info(f"Instagram Reel published: media_id={media_id}")
    return {"media_id": media_id, "status": "published"}


# ---------------------------------------------------------------------------
# Slack helpers
# ---------------------------------------------------------------------------

def _alert_slack(message: str) -> None:
    """Posts an error alert to #content-approval."""
    try:
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel = os.environ.get("SLACK_APPROVAL_CHANNEL_ID", "")
        client.chat_postMessage(channel=channel, text=message)
    except Exception as e:
        logger.error(f"Could not send Slack alert: {e}")


def _log_publish_summary(results: dict) -> None:
    """Posts a publish summary to #ai-news-feed after all platform attempts."""
    lines = ["📹 *VIDEO PUBLISH SUMMARY*"]
    for platform, result in results.items():
        if platform == "cloudinary_url":
            continue
        status = result.get("status", "unknown")
        icon = "✅" if status == "success" else "❌"
        detail = result.get("url") or result.get("post_url") or result.get("error", "")
        lines.append(f"{icon} {platform.capitalize()}: {status} — {detail}")

    try:
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel = os.environ.get("SLACK_NEWS_CHANNEL_ID", "")
        client.chat_postMessage(channel=channel, text="\n".join(lines))
    except Exception as e:
        logger.error(f"Could not post publish summary to Slack: {e}")


# ---------------------------------------------------------------------------
# Part 5: Full publish orchestrator
# ---------------------------------------------------------------------------

def publish_video_all_platforms(
    captioned_video_path: str,
    video_meta: dict,
    linkedin_caption: str,
    youtube_title: str,
    youtube_description: str,
) -> dict:
    """
    Full publish flow after Slack approval:
    1. Upload to Cloudinary → get public URL (required; aborts entire flow on failure)
    2. Publish LinkedIn video (uses Cloudinary URL)
    3. Upload YouTube Short (uses local file path)
    4. Publish Instagram Reel (uses Cloudinary URL)

    TikTok: not auto-published. Videos are downloaded locally; operator posts manually.

    Each platform failure is logged and Slack-alerted independently.
    The orchestrator never raises — failed platforms are recorded in results dict.
    A publish summary is always posted to #ai-news-feed.

    Returns:
    {
        "cloudinary_url": "https://...",
        "linkedin":  {"status": "success"|"failed", "post_url": "...", "error": "..."},
        "youtube":   {"status": "success"|"failed", "video_id": "...", "url": "..."},
        "instagram": {"status": "success"|"failed", "media_id": "...", "error": "..."},
    }
    """
    results: dict = {}
    video_id = video_meta.get("video_id", "video")
    public_id = f"csk-content/{video_id}"

    # 1. Cloudinary — required; abort if this fails
    try:
        cloudinary_url = upload_to_cloudinary(captioned_video_path, public_id)
        results["cloudinary_url"] = cloudinary_url
    except Exception as e:
        logger.error(f"Cloudinary upload failed — aborting video publish: {e}")
        _alert_slack(f"⚠️ *CLOUDINARY UPLOAD FAILED*\nVideo: {video_id}\nError: {e}\nAll platform publishes aborted.")
        return {"cloudinary_url": None, "error": str(e)}

    # 2. LinkedIn
    try:
        li_result = publish_linkedin_video(cloudinary_url, linkedin_caption)
        results["linkedin"] = {
            "status":   "success",
            "post_url": li_result.get("permalink", ""),
            "post_id":  li_result.get("id", ""),
        }
    except Exception as e:
        logger.error(f"LinkedIn video publish failed: {e}")
        _alert_slack(f"⚠️ *LINKEDIN VIDEO FAILED*\nVideo: {video_id}\nError: {e}")
        results["linkedin"] = {"status": "failed", "error": str(e)}

    # 3. YouTube
    try:
        yt_result = publish_youtube_short(captioned_video_path, youtube_title, youtube_description)
        results["youtube"] = {
            "status":   "success",
            "video_id": yt_result.get("video_id", ""),
            "url":      yt_result.get("url", ""),
        }
    except Exception as e:
        logger.error(f"YouTube Short upload failed: {e}")
        _alert_slack(f"⚠️ *YOUTUBE SHORT FAILED*\nVideo: {video_id}\nError: {e}")
        results["youtube"] = {"status": "failed", "error": str(e)}

    # 4. Instagram
    try:
        ig_result = publish_instagram_reel(cloudinary_url, linkedin_caption)
        results["instagram"] = {
            "status":   "success",
            "media_id": ig_result.get("media_id", ""),
        }
    except Exception as e:
        logger.error(f"Instagram Reel publish failed: {e}")
        _alert_slack(f"⚠️ *INSTAGRAM REEL FAILED*\nVideo: {video_id}\nError: {e}")
        results["instagram"] = {"status": "failed", "error": str(e)}

    # Always post summary
    _log_publish_summary(results)
    logger.info(f"Video publish complete — results: {json.dumps({k: v.get('status', v) for k, v in results.items() if k != 'cloudinary_url'})}")

    return results


# ---------------------------------------------------------------------------
# Part 6: Record publication
# ---------------------------------------------------------------------------

def record_publication(video_meta: dict, publish_results: dict, date_str: str) -> None:
    """
    Appends a video publication record to published_content.json.
    Includes video_id, all platform URLs, and idea metadata for performance tracking.
    If the file doesn't exist, creates it.
    """
    record = {
        "date":          date_str,
        "type":          "video",
        "platform":      "multi",
        "video_id":      video_meta.get("video_id", ""),
        "idea_title":    video_meta.get("title", ""),
        "idea_score":    video_meta.get("idea_score"),
        "cloudinary_url": publish_results.get("cloudinary_url", ""),
        "linkedin":      publish_results.get("linkedin", {}),
        "youtube":       publish_results.get("youtube", {}),
        "instagram":     publish_results.get("instagram", {}),
        "published_at":  datetime.now(timezone.utc).isoformat(),
    }

    existing: list = []
    if PUBLISHED_CONTENT_PATH.exists():
        try:
            with open(PUBLISHED_CONTENT_PATH) as f:
                existing = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read published_content.json: {e}")

    existing.append(record)
    with open(PUBLISHED_CONTENT_PATH, "w") as f:
        json.dump(existing, f, indent=2)

    logger.info(f"Publication recorded → published_content.json")


if __name__ == "__main__":
    print("video_publisher.py loaded — run via main_daily.py after approval")
