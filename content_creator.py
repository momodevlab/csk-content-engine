"""
content_creator.py — CSK Content Engine

Core Claude-powered content generation module. Takes a scored idea (Track 1)
or news story (Track 2) and produces every content format: LinkedIn post,
Twitter/X thread, blog post, newsletter section, carousel brief, video script.
"""

import os
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone

from anthropic import Anthropic
from dotenv import load_dotenv

from utils.logger import get_logger
from utils.rate_limiter import api_delay

load_dotenv()
logger = get_logger("content_creator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"

MAX_TOKENS = {
    "linkedin_post":      600,
    "twitter_thread":     800,
    "blog_post":         2500,
    "newsletter_section": 600,
    "carousel_brief":     800,
    "news_linkedin":      500,
    "news_twitter":       700,
    "video_script":       400,
}

VOICE_SYSTEM_PROMPT = """You are writing content for CSK Tech Solutions, a technology services company
specializing in AI, automation, and data engineering.

VOICE RULES — follow these absolutely:
- Write like a brilliant friend who knows tech. Direct, plain English. No fluff.
- Tie everything to business outcomes and ROI. Never just describe features.
- Educational first, never promotional. Earn trust before asking for anything.
- Confident and direct. Say what you mean. No hedging.
- Human and conversational. Contractions are fine. Short sentences are good.

BANNED WORDS — never use these:
synergy, leverage (as verb), circle back, touch base, bandwidth (for people),
cutting-edge, state-of-the-art, best-in-class, disruptive, revolutionizing,
transforming, "we are passionate about", "we are committed to", solutions (standalone noun)

BANNED PATTERNS:
- Em dashes (—) used for dramatic effect
- Parallel tricolon: "We build. We automate. We deliver."
- Starting paragraphs with "In today's..."
- Ending with generic "reach out to learn more"

TARGET AUDIENCE (know who you're writing for):
- Accounting firm owners and managing partners (10-50 staff)
- Insurance agency owners and operations managers
- Marketing agency founders and COOs
- CTOs and VP Engineering at Seed-Series B startups (FinTech, HealthTech, SaaS, InsurTech)"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_claude(client: Anthropic, user_prompt: str, format_key: str, retry: bool = True) -> "Optional[str]":
    """
    Makes a single Claude API call with the shared voice system prompt.
    Retries once after 5 seconds on failure. Returns the response text or None.
    Logs the format name and approximate token usage.
    """
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS[format_key],
            system=VOICE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = response.content[0].text.strip()
        usage = response.usage
        logger.info(f"[{format_key}] Generated — input: {usage.input_tokens}, output: {usage.output_tokens} tokens")
        return text
    except Exception as e:
        if retry:
            logger.warning(f"[{format_key}] API call failed, retrying in 5s: {e}")
            time.sleep(5)
            return _call_claude(client, user_prompt, format_key, retry=False)
        logger.error(f"[{format_key}] API call failed after retry: {e}")
        return None


def _slugify(text: str) -> str:
    """Converts a title string to a filesystem-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:60]


def _save(path: Path, content: str) -> None:
    """Writes content to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    logger.info(f"Saved → {path}")


def _idea_context(idea: dict) -> str:
    """Extracts a concise context string from an idea dict for use in prompts."""
    title = idea.get("title", "")
    body = idea.get("body_preview", "") or idea.get("summary", "")
    hook = idea.get("content_hook", "")
    angle = idea.get("csk_angle_note", "")
    parts = [f"Topic: {title}"]
    if body:
        parts.append(f"Context: {body[:400]}")
    if hook:
        parts.append(f"Suggested hook angle: {hook}")
    if angle:
        parts.append(f"CSK tie-in: {angle}")
    return "\n".join(parts)


def _style_guidance(idea: dict) -> str:
    """
    Builds a style instruction block from viral_style_patterns if available.
    Returns an empty string if no patterns were captured for this idea.
    """
    patterns = idea.get("viral_style_patterns")
    if not patterns:
        return ""

    lines = ["VIRAL STYLE BLUEPRINT (mirror this structure — not the words):"]
    if patterns.get("hook_type"):
        lines.append(f"- Hook type: {patterns['hook_type']}")
    if patterns.get("hook_notes"):
        lines.append(f"  How: {patterns['hook_notes']}")
    if patterns.get("paragraph_rhythm"):
        lines.append(f"- Paragraph rhythm: {patterns['paragraph_rhythm']}")
    if patterns.get("body_structure"):
        lines.append(f"- Body flow: {patterns['body_structure']}")
    if patterns.get("transition_style"):
        lines.append(f"- Transitions: {patterns['transition_style']}")
    if patterns.get("cta_style"):
        lines.append(f"- CTA style: {patterns['cta_style']}")
    if patterns.get("cta_notes"):
        lines.append(f"  How: {patterns['cta_notes']}")
    if patterns.get("emotional_trigger"):
        lines.append(f"- Lead emotional trigger: {patterns['emotional_trigger']}")
    if patterns.get("formatting_notes"):
        lines.append(f"- Formatting: {patterns['formatting_notes']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Format generators — Track 1
# ---------------------------------------------------------------------------

def create_linkedin_post(idea: dict, client: Anthropic) -> "Optional[str]":
    """
    Generates a 150-300 word LinkedIn text post from a Track 1 idea.
    Structure mirrors the viral style patterns extracted from the source post.
    Ends with "What's your experience with this? Drop a comment below."
    Includes 3-5 hashtags. Never starts with "I" or "Just".
    """
    context = _idea_context(idea)
    style = _style_guidance(idea)

    style_block = f"\n\n{style}\n" if style else ""

    prompt = f"""{context}{style_block}
Write a LinkedIn text post for CSK Tech Solutions. Requirements:
- 150-300 words
- Do NOT start with "I" or "Just"
- End with exactly: "What's your experience with this? Drop a comment below."
- Add 3-5 relevant hashtags on the last line (e.g. #Automation #AccountingTech #AITools #CSKTechSolutions)
- Do not sound like a press release. Write like a person.
{f"- Follow the VIRAL STYLE BLUEPRINT above for structure and rhythm. Use the same hook type, body flow, and CTA approach — but with CSK's voice and your own words." if style else "- Line 1 must be a single-line hook: a surprising stat, bold claim, or counterintuitive truth that stops the scroll. Structure after the hook: Problem → Insight → Practical takeaway → CTA."}
- Output only the post text. No preamble, no explanation."""

    return _call_claude(client, prompt, "linkedin_post")


def create_twitter_thread(idea: dict, client: Anthropic) -> "Optional[List[str]]":
    """
    Generates an 8-10 tweet Twitter/X thread from a Track 1 idea.
    Tweet 1 mirrors the viral hook style from the source post.
    Tweets 2-8 are numbered points. Tweet 9 is summary. Tweet 10 is CTA.
    Returns a list of strings (one per tweet), max 280 chars each.
    """
    context = _idea_context(idea)
    style = _style_guidance(idea)

    style_block = f"\n\n{style}\n" if style else ""

    prompt = f"""{context}{style_block}
Write a Twitter/X thread for CSK Tech Solutions. Requirements:
- 8-10 tweets total
- Tweet 1: Scroll-stopping hook — no number prefix.{" Mirror the hook type and emotional trigger from the VIRAL STYLE BLUEPRINT above." if style else " Use the most surprising or counterintuitive insight."}
- Tweets 2-8: Numbered (2/ 3/ etc.), one clear point per tweet
- Tweet 9: Start with "The short version:" — 1-2 sentence summary
- Tweet 10: CTA + "Follow @CSKTechSolutions for more"
- Every tweet must be 280 characters or fewer
- No hashtags except on the final tweet (max 2)
- Return ONLY a JSON array of strings, one string per tweet, no extra text.
Example format: ["Tweet 1 text", "2/ Tweet 2 text", ...]"""

    raw = _call_claude(client, prompt, "twitter_thread")
    if raw is None:
        return None
    try:
        # Strip any markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        tweets = json.loads(cleaned)
        if isinstance(tweets, list):
            return [str(t) for t in tweets]
    except Exception as e:
        logger.warning(f"Twitter thread parse failed, returning raw split: {e}")
        # Fallback: split on newlines and filter empty lines
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return None


def create_blog_post(idea: dict, target_keyword: str, client: Anthropic) -> "Optional[str]":
    """
    Generates an 800-1,200 word SEO blog post in markdown from a Track 1 idea.
    H1 is keyword-rich. 4-6 H2 sections with actionable content.
    Ends with a CTA to book a free AI workflow audit at csktech.solutions.
    Internal link placeholders use [LINK: topic] format.
    """
    context = _idea_context(idea)
    prompt = f"""{context}

Target SEO keyword: "{target_keyword}"

Write a blog post for CSK Tech Solutions in markdown. Requirements:
- 800-1,200 words
- H1: keyword-rich title that includes "{target_keyword}"
- Introduction (100 words): establish the problem clearly, no fluff
- 4-6 H2 sections with actionable, specific content — not vague generalities
- Conclusion: what the reader should do next
- Final CTA (its own paragraph): "Ready to see what this looks like for your business? Book a free AI workflow audit with CSK Tech Solutions at csktech.solutions"
- Tone: educational and authoritative — like an expert who explains things clearly, not a salesperson
- Where a related topic would benefit from a link, use placeholder: [LINK: topic description]
- Output only the markdown. No preamble."""

    return _call_claude(client, prompt, "blog_post")


def create_newsletter_section(idea: dict, client: Anthropic) -> "Optional[str]":
    """
    Generates a 200-300 word newsletter section in markdown from a Track 1 idea.
    Written in Monique's voice — direct, like a smart friend sharing something useful.
    Ends with one concrete action the reader can take this week.
    """
    context = _idea_context(idea)
    prompt = f"""{context}

Write a newsletter section for the CSK Brief (CSK Tech Solutions' weekly newsletter). Requirements:
- 200-300 words
- Voice: Monique (CEO) talking directly to the reader — not a broadcast, not corporate
- Tone: smart friend who read something useful and is passing it along
- End with one concrete action the reader can take THIS WEEK (specific, not vague)
- Return as markdown (use **bold** for emphasis where natural, not for decoration)
- No section header needed — the content stands alone
- Output only the section text. No preamble."""

    return _call_claude(client, prompt, "newsletter_section")


def create_carousel_brief(idea: dict, client: Anthropic) -> "Optional[dict]":
    """
    Generates a 6-8 slide carousel brief as a structured JSON dict from a Track 1 idea.
    Each slide has: slide_number, headline (max 8 words), body (max 20 words), visual_note.
    Slide 1 is the hook, slides 2-6 are insights, slide 7 is the summary takeaway,
    slide 8 is the follow CTA. Design uses navy #0B1120, teal #2DD4BF, white, Inter font.
    """
    context = _idea_context(idea)
    prompt = f"""{context}

Create a LinkedIn carousel brief for CSK Tech Solutions. Return ONLY valid JSON, no extra text.

Requirements:
- 6-8 slides
- Slide 1: Hook headline — same punchy angle as the LinkedIn post hook
- Slides 2-6: One insight per slide (concise, specific, actionable)
- Slide 7: "The key takeaway:" — one-sentence summary
- Slide 8: "Follow CSK Tech Solutions for weekly AI and automation insights"
- Each headline: max 8 words
- Each body: max 20 words (can be empty "" for pure headline slides)
- visual_note: brief design direction for that slide

JSON format:
{{
  "topic": "<topic title>",
  "design": {{
    "background": "#0B1120",
    "accent": "#2DD4BF",
    "text": "#FFFFFF",
    "font": "Inter"
  }},
  "slides": [
    {{
      "slide_number": 1,
      "headline": "<max 8 words>",
      "body": "<max 20 words or empty string>",
      "visual_note": "<brief design note>"
    }}
  ]
}}"""

    raw = _call_claude(client, prompt, "carousel_brief")
    if raw is None:
        return None
    try:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        return json.loads(cleaned)
    except Exception as e:
        logger.warning(f"Carousel brief JSON parse failed: {e}")
        return None


def create_video_script(idea: dict, client: Anthropic) -> "Optional[str]":
    """
    Generates a 130-180 word spoken video script for Monique's HeyGen avatar.
    Structure: Hook → Problem → Insight → Takeaway → CTA (book audit at csktech.solutions).
    Output is the spoken script only — no stage directions, no [PAUSE], no emojis.
    First sentence must earn the next 60 seconds.
    """
    context = _idea_context(idea)
    prompt = f"""{context}

Write a spoken video script for Monique, CEO of CSK Tech Solutions. This will be delivered by an AI avatar (HeyGen). Requirements:
- 130-180 words
- Structure: Hook → Problem → Insight → Takeaway → CTA
- CTA: "Book a free AI workflow audit at csktech.solutions"
- Written for Monique's voice: direct, no buzzwords, plain English, confident
- The first sentence must immediately earn the viewer's attention — no warm-up, no "Hey everyone"
- Do NOT start with "Hey", "Hi", "Hello", or any filler opener
- Output ONLY the spoken script. No stage directions, no [PAUSE], no emojis, no scene notes.
- Every sentence should sound natural when spoken aloud."""

    return _call_claude(client, prompt, "video_script")


# ---------------------------------------------------------------------------
# Format generators — Track 2 (news)
# ---------------------------------------------------------------------------

def create_news_linkedin_post(story: dict, client: Anthropic) -> "Optional[str]":
    """
    Generates a Track 2 LinkedIn news post from a scored news story.
    Format: [Breaking/Just Released/Big News]: headline → plain English summary
    → bullet implications for CSK's audience → CSK tie-in → source + hashtags.
    """
    title = story.get("title", "")
    summary = story.get("summary", "")
    source = story.get("source", "")
    implications = story.get("business_implications", [])
    implications_text = "\n".join(f"- {i}" for i in implications) if implications else ""

    prompt = f"""News story:
Title: {title}
Summary: {summary}
Source: {source}
Pre-identified implications: {implications_text}

Write a LinkedIn post for CSK Tech Solutions covering this news story. Use this exact structure:

[Breaking / Just Released / Big News]: [Concise headline — rewrite if the original is jargon-heavy]

[2-3 sentences in plain English — what actually happened, why it matters]

What this means for [accounting firms / small businesses / startups — pick the most relevant]:
• [Implication 1 — specific and concrete]
• [Implication 2 — specific and concrete]
• [Implication 3 — specific and concrete]

[One sentence connecting this to what CSK builds — don't be salesy, just factual]

Source: {source}
#AI #Automation #[one more relevant hashtag] #CSKTechSolutions

Output only the post. No preamble."""

    return _call_claude(client, prompt, "news_linkedin")


def create_news_twitter_thread(story: dict, client: Anthropic) -> "Optional[List[str]]":
    """
    Generates a 7-tweet Track 2 Twitter/X thread from a scored news story.
    Tweet 1: breaking hook. Tweet 2: what happened. Tweet 3: why it matters.
    Tweet 4: who benefits. Tweet 5: what to do. Tweet 6: CSK angle. Tweet 7: source + CTA.
    Returns a list of strings.
    """
    title = story.get("title", "")
    summary = story.get("summary", "")
    source = story.get("source", "")
    source_url = story.get("source_url", "")

    prompt = f"""News story:
Title: {title}
Summary: {summary}
Source: {source}
URL: {source_url}

Write a 7-tweet Twitter/X thread for CSK Tech Solutions. Exact structure:
1/ Breaking headline + the sharpest hook (why should anyone care right now)
2/ What actually happened — plain English, no jargon
3/ Why this matters for businesses (not just tech people)
4/ Who benefits most from this (be specific — accounting firms? startups? both?)
5/ What businesses should do about it this week (concrete, actionable)
6/ The CSK angle — one tweet on what this means for the kind of work CSK does (no pitch)
7/ Source link + "Follow @CSKTechSolutions for AI and automation news that actually matters to your business"

Every tweet must be 280 characters or fewer.
Return ONLY a JSON array of 7 strings, no extra text.
Example: ["1/ text here", "2/ text here", ...]"""

    raw = _call_claude(client, prompt, "news_twitter")
    if raw is None:
        return None
    try:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
        tweets = json.loads(cleaned)
        if isinstance(tweets, list):
            return [str(t) for t in tweets]
    except Exception as e:
        logger.warning(f"News Twitter thread parse failed, falling back to line split: {e}")
        return [line.strip() for line in raw.splitlines() if line.strip()]
    return None


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

def create_full_content_package(idea: dict, date_str: str) -> dict:
    """
    Takes a scored Track 1 idea and generates all 5 content formats (plus video script
    if idea score >= 7). Saves each to content/{date}/track1/. Returns a dict with all
    generated content and their file paths. Partial content is returned on any failure
    — a failed format logs an error but never crashes the rest.

    Output files:
      content/{date}/track1/linkedin_post.md
      content/{date}/track1/twitter_thread.md
      content/{date}/track1/blog_post.md
      content/{date}/track1/newsletter_section.md
      content/{date}/track1/carousel_brief.json
      content/{date}/track1/video_script.md  (score >= 7 only)
    """
    logger.info(f"Creating Track 1 content package for: {idea.get('title', '')[:60]}")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    out_dir = Path(f"content/{date_str}/track1")
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict = {"idea": idea, "files": {}, "content": {}}

    # Derive a target keyword from the idea title for the blog post
    target_keyword = idea.get("title", "AI automation for business")[:60]

    # Format definitions: (key, generator_fn, args, file_name, is_json)
    formats = [
        ("linkedin_post",      create_linkedin_post,      [idea, client],                      "linkedin_post.md",      False),
        ("twitter_thread",     create_twitter_thread,     [idea, client],                      "twitter_thread.md",     False),
        ("blog_post",          create_blog_post,           [idea, target_keyword, client],      "blog_post.md",          False),
        ("newsletter_section", create_newsletter_section, [idea, client],                      "newsletter_section.md", False),
        ("carousel_brief",     create_carousel_brief,     [idea, client],                      "carousel_brief.json",   True),
    ]

    score = idea.get("scores", {}).get("total", 0)
    if score >= 7:
        formats.append(("video_script", create_video_script, [idea, client], "video_script.md", False))

    for key, fn, args, filename, is_json in formats:
        try:
            output = fn(*args)
            if output is None:
                logger.error(f"[{key}] returned None — skipping")
                continue
            file_path = out_dir / filename
            if is_json:
                content_str = json.dumps(output, indent=2)
            elif isinstance(output, list):
                content_str = "\n\n---\n\n".join(output)
            else:
                content_str = output
            _save(file_path, content_str)
            result["content"][key] = output
            result["files"][key] = str(file_path)
            api_delay()
        except Exception as e:
            logger.error(f"[{key}] generation crashed: {e}")

    logger.info(f"Track 1 package complete: {len(result['files'])}/{len(formats)} formats generated")
    return result


def create_news_content_package(story: dict, date_str: str) -> dict:
    """
    Takes a scored Track 2 news story and generates LinkedIn post + Twitter thread.
    Saves to content/{date}/track2/{story_slug}/. Returns a dict with content and
    file paths. Partial content is returned on any failure.

    Output files:
      content/{date}/track2/{story_slug}/linkedin_post.md
      content/{date}/track2/{story_slug}/twitter_thread.md
    """
    title = story.get("title", "news")
    logger.info(f"Creating Track 2 content package for: {title[:60]}")
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    slug = _slugify(title)
    out_dir = Path(f"content/{date_str}/track2/{slug}")
    out_dir.mkdir(parents=True, exist_ok=True)

    result: dict = {"story": story, "files": {}, "content": {}}

    formats = [
        ("news_linkedin", create_news_linkedin_post,    [story, client], "linkedin_post.md",  False),
        ("news_twitter",  create_news_twitter_thread,   [story, client], "twitter_thread.md", False),
    ]

    for key, fn, args, filename, is_json in formats:
        try:
            output = fn(*args)
            if output is None:
                logger.error(f"[{key}] returned None — skipping")
                continue
            file_path = out_dir / filename
            if isinstance(output, list):
                content_str = "\n\n---\n\n".join(output)
            else:
                content_str = output
            _save(file_path, content_str)
            result["content"][key] = output
            result["files"][key] = str(file_path)
            api_delay()
        except Exception as e:
            logger.error(f"[{key}] generation crashed: {e}")

    logger.info(f"Track 2 package complete: {len(result['files'])}/2 formats generated")
    return result


if __name__ == "__main__":
    # Smoke test with a dummy idea
    dummy_idea = {
        "title": "Accounting firms are spending 40 hours/month on manual reconciliation",
        "body_preview": "Most mid-size accounting firms still reconcile accounts by hand. "
                        "Automation tools exist but adoption is low due to integration complexity.",
        "content_hook": "40 hours a month. That's what reconciliation costs a 20-person accounting firm.",
        "csk_angle_note": "Directly maps to workflow automation + accounting firm ICP",
        "scores": {"total": 9},
    }
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    package = create_full_content_package(dummy_idea, today)
    print(f"Generated formats: {list(package['files'].keys())}")
