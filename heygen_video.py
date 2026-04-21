"""
heygen_video.py — CSK Content Engine

Generates AI avatar videos using the HeyGen REST API v2.
Daily clips: single-scene avatar video (~60s script).
Weekly hero: multi-scene video built from a scene manifest JSON.
"""

import os
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("heygen_video")

HEYGEN_API_BASE = "https://api.heygen.com"
POLL_INTERVAL_SECS = 20
POLL_TIMEOUT_SECS = 1800  # 30 minutes max


def _headers() -> dict:
    return {"X-Api-Key": os.environ["HEYGEN_API_KEY"], "Content-Type": "application/json"}


def _poll_video(video_id: str) -> dict:
    """Polls until the video is completed or fails. Returns the completed video data."""
    deadline = time.time() + POLL_TIMEOUT_SECS
    while time.time() < deadline:
        resp = requests.get(
            f"{HEYGEN_API_BASE}/v1/video_status.get",
            params={"video_id": video_id},
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        status = data.get("status", "")
        logger.info(f"HeyGen video {video_id} status: {status}")
        if status == "completed":
            return data
        elif status in ("failed", "error"):
            raise RuntimeError(f"HeyGen video {video_id} failed: {data.get('error', status)}")
        time.sleep(POLL_INTERVAL_SECS)
    raise RuntimeError(f"HeyGen video {video_id} timed out after {POLL_TIMEOUT_SECS}s")


def _download_video(video_url: str, output_path: str) -> None:
    """Downloads the rendered MP4 from HeyGen CDN to output_path."""
    logger.info(f"Downloading HeyGen video → {output_path}")
    resp = requests.get(video_url, stream=True, timeout=300)
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):
            f.write(chunk)
    logger.info(f"Downloaded: {output_path} ({Path(output_path).stat().st_size // 1024} KB)")


# ---------------------------------------------------------------------------
# Avatar discovery
# ---------------------------------------------------------------------------

def get_correct_avatar_id() -> list[dict]:
    """Returns available avatars from the HeyGen API."""
    resp = requests.get(f"{HEYGEN_API_BASE}/v2/avatars", headers=_headers(), timeout=15)
    resp.raise_for_status()
    avatars = resp.json().get("data", {}).get("avatars", [])
    logger.info(f"Available avatars: {[a.get('avatar_id') for a in avatars[:5]]}")
    return avatars


# ---------------------------------------------------------------------------
# Daily short clip (~60 seconds)
# ---------------------------------------------------------------------------

def generate_daily_video(script: str, output_path: str) -> dict:
    """
    Generates a ~60-second avatar video via HeyGen v2 API.
    Single scene: avatar speaks the full script over a background.
    Downloads the rendered MP4 to output_path.
    Returns the completed video data dict.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    avatar_id = os.environ["HEYGEN_AVATAR_ID"]
    voice_id = os.environ["HEYGEN_VOICE_ID"]

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": voice_id,
                },
                "background": {
                    "type": "color",
                    "value": "#1A3C5E",
                },
            }
        ],
        "aspect_ratio": "9:16",
        "test": False,
    }

    logger.info(f"Submitting HeyGen daily video → {output_path}")
    resp = requests.post(
        f"{HEYGEN_API_BASE}/v2/video/generate",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    video_id = resp.json().get("data", {}).get("video_id")
    if not video_id:
        raise RuntimeError(f"HeyGen did not return a video_id: {resp.json()}")

    logger.info(f"HeyGen video submitted: {video_id}")
    data = _poll_video(video_id)
    video_url = data.get("video_url")
    if not video_url:
        raise RuntimeError(f"HeyGen completed but no video_url in response: {data}")

    _download_video(video_url, output_path)
    return {**data, "output_path": output_path, "video_id": video_id}


# ---------------------------------------------------------------------------
# Weekly hero video (multi-scene manifest — 2-3 minutes)
# ---------------------------------------------------------------------------

def generate_weekly_video(manifest_path: str, output_path: str) -> dict:
    """
    Generates a 2-3 minute weekly hero video from a scene manifest JSON.
    Manifest is generated by content_creator.generate_scene_manifest().
    Each scene becomes a separate video_input with its own script segment.
    Downloads the rendered MP4 to output_path.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    avatar_id = os.environ["HEYGEN_AVATAR_ID"]
    voice_id = os.environ["HEYGEN_VOICE_ID"]

    with open(manifest_path) as f:
        manifest = json.load(f)

    scenes = manifest.get("scenes", [])
    if not scenes:
        raise RuntimeError(f"Manifest has no scenes: {manifest_path}")

    video_inputs = []
    for scene in scenes:
        video_inputs.append({
            "character": {
                "type": "avatar",
                "avatar_id": avatar_id,
                "avatar_style": "normal",
            },
            "voice": {
                "type": "text",
                "input_text": scene.get("script", ""),
                "voice_id": voice_id,
            },
            "background": {
                "type": "color",
                "value": "#1A3C5E",
            },
        })

    payload = {
        "video_inputs": video_inputs,
        "aspect_ratio": "16:9",
        "test": False,
    }

    logger.info(f"Submitting HeyGen weekly video ({len(scenes)} scenes) → {output_path}")
    resp = requests.post(
        f"{HEYGEN_API_BASE}/v2/video/generate",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    video_id = resp.json().get("data", {}).get("video_id")
    if not video_id:
        raise RuntimeError(f"HeyGen did not return a video_id: {resp.json()}")

    logger.info(f"HeyGen weekly video submitted: {video_id}")
    data = _poll_video(video_id)
    video_url = data.get("video_url")
    if not video_url:
        raise RuntimeError(f"HeyGen completed but no video_url: {data}")

    _download_video(video_url, output_path)
    return {**data, "output_path": output_path, "video_id": video_id}


# ---------------------------------------------------------------------------
# Full daily pipeline entry point (called from main_daily.py)
# ---------------------------------------------------------------------------

def create_avatar_video(idea: dict, date_str: str, video_script: str) -> dict | None:
    """
    Full pipeline for one daily video: generate via HeyGen API → save metadata.
    Takes the pre-written video_script from content_creator.
    Saves to content/{date}/track3/. Returns metadata dict or None on failure.
    """
    score = idea.get("scores", {}).get("total", 0)
    title = idea.get("title", "CSK Tech Video")[:80]
    out_dir = Path(f"content/{date_str}/track3")
    out_dir.mkdir(parents=True, exist_ok=True)

    slug = title.lower().replace(" ", "_")[:40]
    output_path = str(out_dir / f"daily_{slug}.mp4")

    try:
        logger.info(f"=== HeyGen daily pipeline: {title} ===")
        result = generate_daily_video(video_script, output_path)

        meta = {
            "title":          title,
            "script":         video_script,
            "raw_video_path": output_path,
            "video_id":       result.get("video_id", ""),
            "idea_score":     score,
            "status":         "rendered",
            "created_at":     datetime.now(timezone.utc).isoformat(),
        }
        meta_path = out_dir / f"video_meta_{slug}.json"
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        logger.info(f"=== HeyGen daily pipeline complete: {output_path} ===")
        return meta

    except Exception as e:
        logger.error(f"HeyGen daily pipeline failed for '{title}': {e}")
        return None


if __name__ == "__main__":
    print("Available avatars:")
    try:
        avatars = get_correct_avatar_id()
        for a in avatars[:10]:
            print(f"  {a.get('avatar_id')} — {a.get('avatar_name', '')}")
    except Exception as e:
        print(f"Error: {e}")
