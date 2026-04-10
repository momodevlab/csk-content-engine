"""
caption_video.py — CSK Content Engine

Transcribes video audio via OpenAI Whisper and burns SRT captions into the
video using ffmpeg. Produces a captioned MP4 ready for platform publishing.

Requires ffmpeg to be installed (check_dependencies() validates this on import).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

from utils.logger import get_logger
from utils.rate_limiter import api_delay

load_dotenv()
logger = get_logger("caption_video")

CHARS_PER_LINE = 28
FILLER_WORDS   = {"a", "the", "an", "and", "or", "but", "so", "of", "to", "in"}

CAPTION_STYLE = (
    "FontName=Arial,"
    "FontSize=13,"
    "PrimaryColour=&HFFFFFF,"
    "OutlineColour=&H000000,"
    "Outline=2,"
    "Bold=1,"
    "Alignment=2,"
    "MarginV=90"
)


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_dependencies() -> None:
    """
    Verifies that ffmpeg is installed and accessible. Checks system PATH first,
    then common Homebrew locations (/opt/homebrew/bin, /usr/local/bin).
    Raises RuntimeError with install instructions if not found anywhere.
    Called at module import time so failures are caught early.
    """
    HOMEBREW_PATHS = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]
    if shutil.which("ffmpeg") or any(os.path.isfile(p) for p in HOMEBREW_PATHS):
        # Ensure Homebrew bin is on PATH for subprocess calls
        for brew_bin in ["/opt/homebrew/bin", "/usr/local/bin"]:
            if brew_bin not in os.environ.get("PATH", ""):
                os.environ["PATH"] = brew_bin + ":" + os.environ.get("PATH", "")
        return
    raise RuntimeError(
        "ffmpeg not found. Install with:\n"
        "  Mac:   brew install ffmpeg\n"
        "  Linux: sudo apt-get install -y ffmpeg\n"
        "  Windows: https://ffmpeg.org/download.html"
    )


check_dependencies()


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_video(video_path: str) -> list[dict]:
    """
    Sends the video file to OpenAI Whisper (whisper-1) with word-level
    timestamp granularity. Returns a list of word dicts:
    [{"word": "...", "start": 0.0, "end": 0.5}, ...]

    Uses verbose_json response format to access word timestamps.
    Falls back to segment-level timestamps if word-level is unavailable.
    """
    logger.info(f"Transcribing: {video_path}")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    with open(video_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    words = []
    # Prefer word-level timestamps
    if hasattr(response, "words") and response.words:
        for w in response.words:
            words.append({
                "word":  w.word.strip(),
                "start": w.start,
                "end":   w.end,
            })
        logger.info(f"Transcription complete: {len(words)} words with word-level timestamps")
        return words

    # Fallback: distribute segment text evenly across segment duration
    logger.warning("Word-level timestamps unavailable — falling back to segment-level")
    for seg in (response.segments or []):
        seg_words = seg.text.strip().split()
        if not seg_words:
            continue
        duration = (seg.end - seg.start) / len(seg_words)
        for i, word in enumerate(seg_words):
            words.append({
                "word":  word,
                "start": seg.start + i * duration,
                "end":   seg.start + (i + 1) * duration,
            })

    logger.info(f"Transcription complete: {len(words)} words (segment fallback)")
    return words


# ---------------------------------------------------------------------------
# SRT builder
# ---------------------------------------------------------------------------

def _format_srt_time(seconds: float) -> str:
    """Converts a float seconds value to SRT timestamp format: HH:MM:SS,mmm"""
    ms = int((seconds % 1) * 1000)
    s  = int(seconds) % 60
    m  = int(seconds) // 60 % 60
    h  = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def words_to_srt(words: list[dict], chars_per_line: int = CHARS_PER_LINE) -> str:
    """
    Groups words into caption chunks of at most chars_per_line characters.
    Rules:
    - Never split mid-word
    - Never end a line on a filler word (a, the, an, and, or, but, so, of, to, in)
    - Each caption block spans from the start of its first word to the end of its last
    Returns a valid SRT format string.
    """
    if not words:
        return ""

    # Group words into lines
    chunks: list[list[dict]] = []
    current_chunk: list[dict] = []
    current_len = 0

    for word_dict in words:
        word = word_dict["word"]
        # +1 for the space separator
        addition = len(word) + (1 if current_chunk else 0)

        if current_len + addition > chars_per_line and current_chunk:
            # Don't end on a filler word — push it to the next chunk
            while current_chunk and current_chunk[-1]["word"].lower().rstrip(".,!?") in FILLER_WORDS:
                overflow = current_chunk.pop()
                current_len -= len(overflow["word"]) + 1

            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = [overflow] if 'overflow' in dir() and current_chunk == [] else []
            # Reset overflow tracking
            try:
                current_chunk = [overflow]
                current_len = len(overflow["word"])
            except UnboundLocalError:
                current_chunk = []
                current_len = 0

        current_chunk.append(word_dict)
        current_len += addition

    if current_chunk:
        chunks.append(current_chunk)

    # Build SRT
    srt_blocks = []
    for i, chunk in enumerate(chunks, 1):
        start_ts = _format_srt_time(chunk[0]["start"])
        end_ts   = _format_srt_time(chunk[-1]["end"])
        text     = " ".join(w["word"] for w in chunk)
        srt_blocks.append(f"{i}\n{start_ts} --> {end_ts}\n{text}\n")

    return "\n".join(srt_blocks)


# ---------------------------------------------------------------------------
# Caption burning
# ---------------------------------------------------------------------------

def burn_captions(video_path: str, srt_path: str, output_path: str) -> str:
    """
    Calls ffmpeg via subprocess to burn the SRT subtitle file directly into
    the video (hardcoded subtitles). Audio stream is copied without re-encoding.
    Raises RuntimeError if ffmpeg exits with a non-zero code.
    Returns output_path on success.
    """
    # ffmpeg requires escaped colons in Windows paths; srt path must not have spaces
    safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-vf", f"subtitles={safe_srt}:force_style='{CAPTION_STYLE}'",
        "-c:a", "copy",
        output_path,
        "-y",  # overwrite output without prompting
    ]

    logger.info(f"Burning captions: {srt_path} → {output_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"ffmpeg stderr: {result.stderr[-500:]}")
        raise RuntimeError(
            f"ffmpeg failed (exit {result.returncode}). "
            f"Stderr: {result.stderr[-200:]}"
        )

    logger.info(f"Captioned video saved → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def add_captions(raw_video_path: str, date_str: str) -> str:
    """
    Full caption pipeline: transcribe → build SRT → burn captions.

    Derives output paths from the input video path:
      {base}.srt                → SRT subtitle file
      {base}_captioned.mp4      → final video with burned captions

    Both files are saved alongside the raw video in content/{date}/track3/.
    Returns the path to the captioned video.
    Raises on any step failure (caller wraps in try/except).
    """
    base = Path(raw_video_path).with_suffix("")
    srt_path      = str(base) + ".srt"
    captioned_path = str(base) + "_captioned.mp4"

    # 1. Transcribe
    words = transcribe_video(raw_video_path)
    api_delay()

    # 2. Build SRT
    srt_content = words_to_srt(words)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    logger.info(f"SRT saved → {srt_path} ({len(srt_content.splitlines())} lines)")

    # 3. Burn captions
    burn_captions(raw_video_path, srt_path, captioned_path)

    return captioned_path


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python caption_video.py <path_to_video.mp4>")
        sys.exit(1)

    from datetime import datetime, timezone
    video = sys.argv[1]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output = add_captions(video, today)
    print(f"Captioned video: {output}")
