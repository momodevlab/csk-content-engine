"""
remotion_renderer.py — CSK Content Engine

Python wrapper for triggering Remotion renders from the content pipeline.
Remotion runs inside remotion-videos/ and outputs MP4s to content/{date}/track3/.
"""

import json
import os
import subprocess
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("remotion")


def _run_remotion(composition: str, props: dict, output_path: str) -> str:
    """
    Internal helper. Runs `npx remotion render` inside the remotion-videos/ directory.
    output_path is relative to the project root (e.g. content/2026-03-29/track3/stat.mp4).
    The path passed to Remotion is prefixed with ../ to step out of remotion-videos/.
    Returns output_path on success. Raises RuntimeError on non-zero exit.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "npx", "remotion", "render",
        "src/index.ts",
        composition,
        f"../{output_path}",
        f"--props={json.dumps(props)}",
        "--log=verbose",
    ]

    logger.info(f"Rendering {composition} → {output_path}")
    result = subprocess.run(
        cmd,
        cwd="remotion-videos",
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(f"Remotion render failed:\n{result.stderr[-500:]}")
        raise RuntimeError(f"Remotion render failed: {result.stderr[:500]}")

    logger.info(f"Remotion render complete: {output_path}")
    return output_path


def render_stat_video(
    stat: int,
    context_line: str,
    insight_line: str,
    stat_prefix: str = "",
    stat_suffix: str = "",
    date_str: str = None,
    output_name: str = "stat_reveal.mp4",
) -> str:
    """
    Renders a StatReveal composition — a kinetic count-up animation of a bold stat.
    Only run this when the idea contains a clear quantifiable number.

    Args:
        stat:         The number to count up to (e.g. 40)
        context_line: Text above the stat (e.g. "Accounting firms spend")
        insight_line: Text below the stat (e.g. "hours/month on manual entry")
        stat_prefix:  Optional prefix (e.g. "$")
        stat_suffix:  Optional suffix (e.g. " hrs" or "%")
        date_str:     Date string for output path (e.g. "2026-03-29")
        output_name:  Filename for the rendered MP4

    Returns the output file path.
    """
    props = {
        "stat":        stat,
        "statPrefix":  stat_prefix,
        "statSuffix":  stat_suffix,
        "contextLine": context_line,
        "insightLine": insight_line,
        "ctaLine":     "csktech.solutions",
    }
    output_path = f"content/{date_str}/track3/{output_name}"
    return _run_remotion("StatReveal", props, output_path)


def render_before_after_video(
    before_label: str,
    before_stats: list[str],
    after_label: str,
    after_stats: list[str],
    savings_stat: str,
    date_str: str,
    output_name: str = "before_after.mp4",
) -> str:
    """
    Renders a BeforeAfter composition — two panels showing manual vs automated contrast.

    Args:
        before_label:  Label for the manual process panel (e.g. "Manual reconciliation")
        before_stats:  Up to 3 stat strings for the before panel
        after_label:   Label for the automated panel (e.g. "With CSK automation")
        after_stats:   Up to 3 stat strings for the after panel
        savings_stat:  The headline difference (e.g. "95% time saved")
        date_str:      Date string for output path
        output_name:   Filename for the rendered MP4

    Returns the output file path.
    """
    props = {
        "beforeLabel": before_label,
        "beforeStats": before_stats[:3],
        "afterLabel":  after_label,
        "afterStats":  after_stats[:3],
        "savingsStat": savings_stat,
    }
    output_path = f"content/{date_str}/track3/{output_name}"
    return _run_remotion("BeforeAfter", props, output_path)


def render_news_flash(
    headline: str,
    source: str,
    implications: list[str],
    csk_context: str,
    date_str: str,
    slug: str,
) -> str:
    """
    Renders a NewsFlash composition for Track 2 breaking AI news.
    15-second video with headline type-in, staggered implications, and CTA.

    Args:
        headline:        The news story headline
        source:          Publication name (e.g. "VentureBeat")
        implications:    Exactly 3 business implication strings
        csk_context:     One sentence on the CSK angle
        date_str:        Date string for output path
        slug:            Story slug for the output subdirectory

    Returns the output file path.
    """
    props = {
        "headline":    headline,
        "source":      source,
        "implications": implications[:3],
        "cskContext":  csk_context,
    }
    output_path = f"content/{date_str}/track2/{slug}/news_flash.mp4"
    return _run_remotion("NewsFlash", props, output_path)
