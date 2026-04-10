"""
heygen_video.py — CSK Content Engine

Generates AI avatar videos using HeyGen API v2 with Monique's digital twin.
Pipeline: write script → submit to HeyGen → poll status → download MP4.
Only runs for ideas with a score >= 7.
"""

import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from anthropic import Anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()
logger = get_logger("heygen_video")

HEYGEN_GENERATE_URL = "https://api.heygen.com/v2/video/generate"
HEYGEN_STATUS_URL   = "https://api.heygen.com/v1/video_status.get"
POLL_INTERVAL_SECS  = 30

SCRIPT_SYSTEM_PROMPT = """You are writing a short video script for Monique, CEO of CSK Tech Solutions.

MONIQUE'S VOICE:
- Direct and plain English. No buzzwords, no hedging.
- Confident but not arrogant. She's the brilliant friend who knows tech.
- Educational first. She earns trust before asking for anything.
- She talks to accounting firm owners, agency founders, and startup CTOs.

SCRIPT RULES:
- 130-180 words MAXIMUM (60-90 seconds spoken at normal pace)
- Structure: Hook (1 surprising stat or bold claim) → Problem (the pain) → Insight (the thing most people miss) → Takeaway (what to do) → CTA
- CTA must be: "Book a free AI workflow audit at csktech.solutions"
- First sentence must hook immediately — no warm-up, no "Hey everyone"
- Never use: synergy, leverage, circle back, cutting-edge, revolutionize
- Never use em dashes (—) for dramatic effect
- Write for speech — short sentences, natural rhythm
- Output ONLY the spoken script. No [PAUSE], no stage directions, no emojis."""


# ---------------------------------------------------------------------------
# Script writer
# ---------------------------------------------------------------------------

def write_video_script(idea: dict) -> str:
    """
    Uses Claude to write a 130-180 word spoken script for Monique's avatar.
    Takes the idea title, body preview, CSK angle, and content hook as context.
    Returns clean spoken text only — no directions, no formatting.
    Raises on API failure (caller wraps in try/except).
    """
    title      = idea.get("title", "")
    body       = idea.get("body_preview", "") or idea.get("summary", "")
    hook       = idea.get("content_hook", "")
    angle      = idea.get("csk_angle_note", "")

    user_prompt = (
        f"Topic: {title}\n"
        f"Context: {body[:400]}\n"
        f"Suggested hook angle: {hook}\n"
        f"CSK tie-in: {angle}\n\n"
        "Write the video script now. Output only the spoken words."
    )

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=SCRIPT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    script = response.content[0].text.strip()
    word_count = len(script.split())
    logger.info(f"Script written: {word_count} words")
    return script


# ---------------------------------------------------------------------------
# Video generation
# ---------------------------------------------------------------------------

def _heygen_headers() -> dict:
    """Returns standard HeyGen API headers."""
    return {
        "X-Api-Key": os.environ["HEYGEN_API_KEY"],
        "Content-Type": "application/json",
    }


def generate_video(script: str, title: str) -> str:
    """
    Submits the script to HeyGen API v2 for avatar video rendering.
    On 429 rate limit, waits 60 seconds and retries once.
    Returns the video_id string for status polling.
    Raises RuntimeError on unrecoverable failure.
    """
    payload = {
        "video_inputs": [{
            "character": {
                "type": "avatar",
                "avatar_id": os.environ["HEYGEN_AVATAR_ID"],
                "avatar_style": "normal",
            },
            "voice": {
                "type": "text",
                "input_text": script,
                "voice_id": os.environ["HEYGEN_VOICE_ID"],
                "speed": 1.05,
            },
            "background": {
                "type": "color",
                "value": "#0B1120",
            },
        }],
        "dimension": {"width": 1080, "height": 1920},
        "aspect_ratio": "9:16",
        "title": title[:100],
    }

    for attempt in range(2):
        try:
            resp = requests.post(
                HEYGEN_GENERATE_URL,
                headers=_heygen_headers(),
                json=payload,
                timeout=30,
            )
            if resp.status_code == 429 and attempt == 0:
                logger.warning("HeyGen rate limit — waiting 60s before retry")
                time.sleep(60)
                continue
            resp.raise_for_status()
            data = resp.json()
            video_id = data.get("data", {}).get("video_id") or data.get("video_id")
            if not video_id:
                raise RuntimeError(f"HeyGen did not return a video_id: {data}")
            logger.info(f"HeyGen video submitted: id={video_id}")
            return video_id
        except requests.RequestException as e:
            if attempt == 1:
                raise RuntimeError(f"HeyGen generate request failed: {e}")

    raise RuntimeError("HeyGen generate request failed after retry")


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------

def poll_video_status(video_id: str, timeout_minutes: int = 20) -> str:
    """
    Polls HeyGen GET /v1/video_status.get every 30 seconds until the video
    reaches "completed" or "failed" status.
    Returns the download URL on success.
    Raises TimeoutError after timeout_minutes.
    Raises RuntimeError if HeyGen reports "failed".
    Handles intermediate statuses: pending, processing, waiting.
    """
    timeout_secs = timeout_minutes * 60
    elapsed = 0

    logger.info(f"Polling HeyGen status for video_id={video_id} (timeout: {timeout_minutes}m)")

    while elapsed < timeout_secs:
        try:
            resp = requests.get(
                HEYGEN_STATUS_URL,
                headers=_heygen_headers(),
                params={"video_id": video_id},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json().get("data", {})
            status = data.get("status", "unknown")
            logger.info(f"HeyGen status: {status} (elapsed: {elapsed}s)")

            if status == "completed":
                url = data.get("video_url") or data.get("download_url", "")
                if not url:
                    raise RuntimeError(f"HeyGen completed but no download URL: {data}")
                logger.info(f"Video ready: {url}")
                return url

            elif status == "failed":
                error = data.get("error", {})
                raise RuntimeError(f"HeyGen render failed: {error}")

            # pending / processing / waiting — keep polling
            time.sleep(POLL_INTERVAL_SECS)
            elapsed += POLL_INTERVAL_SECS

        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"Poll request error (will retry): {e}")
            time.sleep(POLL_INTERVAL_SECS)
            elapsed += POLL_INTERVAL_SECS

    raise TimeoutError(
        f"HeyGen video {video_id} did not complete within {timeout_minutes} minutes"
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_video(url: str, output_path: str) -> str:
    """
    Downloads the rendered MP4 from HeyGen using streaming to avoid loading
    the entire file into memory. Creates parent directories as needed.
    Returns the output_path on success.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading video → {output_path}")

    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    logger.info(f"Download complete: {size_mb:.1f} MB")
    return output_path


# ---------------------------------------------------------------------------
# Slack alert helper
# ---------------------------------------------------------------------------

def _alert_slack(message: str) -> None:
    """Posts an error alert to #content-approval."""
    try:
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel = os.environ.get("SLACK_APPROVAL_CHANNEL_ID", "")
        client.chat_postMessage(channel=channel, text=message)
    except Exception as e:
        logger.error(f"Could not send Slack alert: {e}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def create_avatar_video(idea: dict, date_str: str) -> "Optional[dict]":
    """
    Full HeyGen pipeline: write script → generate → poll → download.
    Only runs if idea score >= 7. Wraps everything in try/except so a
    HeyGen failure never crashes main_daily.py.

    Saves:
      content/{date}/track3/raw_{video_id}.mp4
      content/{date}/track3/video_meta.json

    Returns:
    {
        "video_id": "...",
        "script": "...",
        "raw_video_path": "content/{date}/track3/raw_{video_id}.mp4",
        "title": "...",
        "idea_score": X,
        "status": "rendered"
    }
    Returns None on any failure.
    """
    score = idea.get("scores", {}).get("total", 0)
    if score < 7:
        logger.info(f"Skipping video — idea score {score} < 7")
        return None

    title = idea.get("title", "CSK Tech Video")[:80]
    out_dir = Path(f"content/{date_str}/track3")
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Write script
        logger.info(f"=== HeyGen pipeline starting: {title} ===")
        script = write_video_script(idea)

        # 2. Generate
        video_id = generate_video(script, title)

        # 3. Poll
        download_url = poll_video_status(video_id)

        # 4. Download
        raw_path = str(out_dir / f"raw_{video_id}.mp4")
        download_video(download_url, raw_path)

        # 5. Save metadata
        meta = {
            "video_id":       video_id,
            "script":         script,
            "raw_video_path": raw_path,
            "title":          title,
            "idea_score":     score,
            "status":         "rendered",
            "created_at":     datetime.now(timezone.utc).isoformat(),
        }
        meta_path = out_dir / "video_meta.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        logger.info(f"=== HeyGen pipeline complete: {video_id} ===")
        return meta

    except TimeoutError as e:
        logger.error(f"HeyGen timeout: {e}")
        _alert_slack(f"⚠️ *HEYGEN TIMEOUT*\nVideo render timed out for: {title}\n{e}")
        return None
    except RuntimeError as e:
        logger.error(f"HeyGen render failed: {e}")
        _alert_slack(f"⚠️ *HEYGEN RENDER FAILED*\nTopic: {title}\nError: {e}")
        return None
    except Exception as e:
        logger.error(f"HeyGen pipeline unexpected error: {e}")
        _alert_slack(f"⚠️ *HEYGEN PIPELINE ERROR*\nTopic: {title}\nError: {e}")
        return None


if __name__ == "__main__":
    dummy = {
        "title": "Accounting firms are spending 40 hours/month on manual reconciliation",
        "body_preview": "Most mid-size accounting firms still reconcile accounts by hand.",
        "content_hook": "40 hours a month. That's what reconciliation costs a 20-person firm.",
        "csk_angle_note": "Directly maps to workflow automation + accounting ICP",
        "scores": {"total": 9},
    }
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = create_avatar_video(dummy, today)
    print(json.dumps(result, indent=2) if result else "Video creation failed")
