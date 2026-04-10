"""
newsletter_builder.py — CSK Content Engine

Runs every Friday at 8 AM CST. Collects the week's Track 1 newsletter sections
and top Track 2 news stories, compiles a full HTML issue with Claude, generates
subject line options, and sends via the GHL Email API.

Newsletter: "The CSK Brief — AI & Automation for Business"
"""

import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from anthropic import Anthropic
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

from utils.logger import get_logger

load_dotenv()
logger = get_logger("newsletter_builder")

GHL_BASE_URL    = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"


# ---------------------------------------------------------------------------
# Step 1: Collect week's content
# ---------------------------------------------------------------------------

def collect_week_content(week_start: str) -> dict:
    """
    Reads all newsletter_section.md files from content/{date}/track1/ for
    Monday through Thursday of the current week (since this runs Friday).
    Also reads the top 3 Track 2 stories by score from scored_news.json files
    written during the same week.

    week_start: Monday's date as "2026-03-23"
    Returns: {"sections": [...], "top_news": [...], "week_dates": [...]}
    """
    logger.info(f"Collecting week content from {week_start}")
    start_dt = datetime.strptime(week_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    week_dates = [
        (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(4)  # Mon–Thu
    ]

    sections = []
    for date_str in week_dates:
        section_path = Path(f"content/{date_str}/track1/newsletter_section.md")
        if section_path.exists():
            try:
                text = section_path.read_text().strip()
                if text:
                    sections.append({"date": date_str, "content": text})
                    logger.info(f"Loaded newsletter section from {date_str}")
            except Exception as e:
                logger.warning(f"Could not read section for {date_str}: {e}")

    logger.info(f"Found {len(sections)} newsletter sections this week")

    # Collect top Track 2 news — gather all scored_news.json from the week
    all_news: list[dict] = []
    for date_str in week_dates:
        news_path = Path(f"content/{date_str}/track2/scored_news.json")
        if news_path.exists():
            try:
                stories = json.loads(news_path.read_text())
                all_news.extend(stories)
            except Exception as e:
                logger.warning(f"Could not read scored_news for {date_str}: {e}")

    # Sort by score and take top 3
    all_news.sort(key=lambda s: s.get("scores", {}).get("total", 0), reverse=True)
    top_news = all_news[:3]
    logger.info(f"Found {len(all_news)} Track 2 stories, using top {len(top_news)}")

    return {"sections": sections, "top_news": top_news, "week_dates": week_dates}


# ---------------------------------------------------------------------------
# Step 2: Compile full issue with Claude
# ---------------------------------------------------------------------------

def compile_newsletter(week_content: dict, week_start: str) -> str:
    """
    Uses Claude to assemble the week's sections and top news into a single
    cohesive HTML email issue. If fewer than 2 newsletter sections exist,
    pads with Track 2 news stories.
    Returns full HTML email body as a string.
    """
    sections = week_content.get("sections", [])
    top_news = week_content.get("top_news", [])

    # Fallback: if fewer than 2 sections, use news stories to fill
    if len(sections) < 2:
        logger.warning(
            f"Only {len(sections)} newsletter section(s) found — padding with Track 2 news"
        )

    # Build the insight block: use the highest-scoring section
    insight_content = sections[0]["content"] if sections else (
        top_news[0].get("title", "No insight available this week") if top_news else
        "No content available this week."
    )

    # Build news block
    news_items_text = ""
    for story in top_news:
        title = story.get("title", "")
        summary = story.get("summary", "")
        source = story.get("source", "")
        implications = story.get("business_implications", [])
        impl_text = implications[0] if implications else ""
        news_items_text += (
            f"\nStory: {title}\nSummary: {summary}\n"
            f"Source: {source}\nBusiness implication: {impl_text}\n"
        )

    week_label = datetime.strptime(week_start, "%Y-%m-%d").strftime("%B %d, %Y")

    user_prompt = f"""Week of: {week_label}

INSIGHT SECTION (use as "THE INSIGHT" block — do not rewrite, only smooth and integrate):
{insight_content}

TOP AI NEWS STORIES (use for "THIS WEEK IN AI" block):
{news_items_text if news_items_text else "No news stories available this week."}

Additional sections if available:
{chr(10).join(s['content'] for s in sections[1:]) if len(sections) > 1 else "(none)"}

Assemble the full HTML newsletter issue. Use this exact structure:
1. Header: THE CSK BRIEF / AI & Automation for Business / Week of {week_label}
2. THIS WEEK IN AI — 2-3 sentences per story, plain English, what it means for the reader
3. THE INSIGHT — the full insight section provided above
4. TOOL OF THE WEEK — invent one relevant AI tool our audience would find useful (real tool if you know one, plausible if not)
5. FROM THE CSK DESK — one authentic sentence about what CSK is building this week
6. WORK WITH US — "Ready to stop doing manually what should run automatically? Book a free 30-minute AI workflow audit: csktech.solutions/audit"
7. Footer — "CSK Tech Solutions | Unsubscribe | csktech.solutions"

Make it feel like one cohesive email. Smooth transitions. Cut redundancy.
Output HTML email body only. No subject line. No markdown code blocks.

HTML requirements:
- Inline styles only (email client compatibility)
- Background: #0B1120 (navy), Accent: #2DD4BF (teal), Text: #FFFFFF (white)
- Font: Arial, Helvetica, sans-serif
- Max width 600px, centered
- Section dividers: a 1px teal horizontal rule
- Links styled in teal
- Mobile-friendly: no fixed widths on inner elements"""

    system_prompt = (
        "You are assembling the weekly CSK Brief newsletter. "
        "Voice: Smart friend sharing useful things, not a corporate broadcast. "
        "Monique (CEO) is the implied author — direct, no buzzwords, outcome-focused. "
        "Make it feel like one cohesive email, not a list of pasted sections. "
        "Smooth out transitions between sections. Cut anything redundant. "
        "Output HTML email body only — no subject line, no markdown code blocks. "
        "Use clean HTML with inline styles (email client compatibility). "
        "Brand colors: background #0B1120 navy, accent #2DD4BF teal, text white. "
        "Font: system fonts only for email (Arial, Helvetica, sans-serif)."
    )

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        html = response.content[0].text.strip()
        logger.info(f"Newsletter compiled — {response.usage.output_tokens} output tokens")
        return html
    except Exception as e:
        logger.error(f"Newsletter compilation failed: {e}")
        raise


# ---------------------------------------------------------------------------
# Step 3: Generate subject lines
# ---------------------------------------------------------------------------

def generate_subject_lines(newsletter_content: str) -> list[str]:
    """
    Sends the compiled newsletter to Claude and asks for 3 subject line options.
    Rules: 40-55 chars, lead with most interesting insight, no emojis, don't start
    with "The CSK Brief". Returns list of 3 subject line strings.
    Falls back to a generic subject line on failure.
    """
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = (
        "Read this newsletter and write 3 subject line options.\n\n"
        "Rules:\n"
        "- 40-55 characters each\n"
        "- Lead with the most interesting insight or news from this issue\n"
        "- Never start with 'The CSK Brief'\n"
        "- No emojis\n"
        "- Make someone want to open it without being clickbait\n"
        "- Examples of the right style: "
        "'Why accounting firms are still reconciling by hand' / "
        "'OpenAI's new model and what it means for your ops'\n\n"
        "Return ONLY a JSON array of 3 strings, no extra text.\n\n"
        f"Newsletter:\n{newsletter_content[:3000]}"
    )
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        import re
        raw = response.content[0].text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
        subjects = json.loads(cleaned)
        if isinstance(subjects, list) and subjects:
            logger.info(f"Generated {len(subjects)} subject line options")
            return [str(s) for s in subjects[:3]]
    except Exception as e:
        logger.warning(f"Subject line generation failed: {e}")

    week_label = datetime.now(timezone.utc).strftime("%B %d")
    return [f"This week in AI automation — {week_label}"]


# ---------------------------------------------------------------------------
# Step 4: Send via GHL Email API
# ---------------------------------------------------------------------------

def send_newsletter(html_body: str, subject: str) -> dict:
    """
    Sends the compiled newsletter to the GHL email list via the GHL Email API.
    Retries once on 429 (waits 60s) or 5xx (waits 10s). Logs the send attempt.
    Returns the GHL API response dict.
    """
    url = f"{GHL_BASE_URL}/email/schedule"
    headers = {
        "Authorization": f"Bearer {os.environ['GHL_API_KEY']}",
        "Version": GHL_API_VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "locationId":   os.environ["GHL_LOCATION_ID"],
        "subject":      subject,
        "body":         html_body,
        "fromEmail":    os.environ.get("GHL_FROM_EMAIL", "newsletter@csktech.solutions"),
        "fromName":     "Monique | CSK Tech Solutions",
        "replyToEmail": os.environ.get("GHL_REPLY_TO_EMAIL", "hello@csktech.solutions"),
        "scheduledAt":  "immediate",
    }

    logger.info(f"Sending newsletter — subject: '{subject}'")

    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 429 and attempt == 0:
                logger.warning("GHL rate limit — waiting 60s before retry")
                time.sleep(60)
                continue
            elif resp.status_code >= 500 and attempt == 0:
                logger.warning(f"GHL server error {resp.status_code} — retrying in 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
            result = resp.json()
            logger.info(f"Newsletter sent successfully: {result.get('id', '—')}")
            return result
        except requests.RequestException as e:
            if attempt == 1:
                logger.error(f"Newsletter send failed after retry: {e}")
                raise

    return {}


# ---------------------------------------------------------------------------
# Step 5: Save compiled issue
# ---------------------------------------------------------------------------

def save_newsletter_issue(html_body: str, subject: str, week_start: str) -> str:
    """
    Saves the compiled issue as both HTML and plain-text-friendly markdown
    to newsletter/{week_start}/. Returns the HTML file path.
    """
    out_dir = Path(f"newsletter/{week_start}")
    out_dir.mkdir(parents=True, exist_ok=True)

    html_path = out_dir / "full_issue.html"
    md_path = out_dir / "full_issue.md"

    html_path.write_text(html_body)

    # Minimal markdown version: strip HTML tags
    import re
    plain = re.sub(r"<[^>]+>", "", html_body)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    md_content = f"# {subject}\n\nWeek of {week_start}\n\n{plain}"
    md_path.write_text(md_content)

    logger.info(f"Newsletter issue saved → {html_path}")
    return str(html_path)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _current_week_monday() -> str:
    """Returns the ISO date string for this week's Monday."""
    today = datetime.now(timezone.utc)
    monday = today - timedelta(days=today.weekday())
    return monday.strftime("%Y-%m-%d")


def _alert_slack(message: str) -> None:
    """Posts an alert to #content-performance on failure."""
    try:
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        channel = os.environ.get("SLACK_PERFORMANCE_CHANNEL_ID", "")
        client.chat_postMessage(channel=channel, text=message)
    except Exception as e:
        logger.error(f"Could not post Slack alert: {e}")


def run_friday_newsletter(week_start: str = None) -> dict:
    """
    Full pipeline: collect → compile → subject lines → send → save.

    Execution order:
    1. Determine week_start (defaults to current week's Monday)
    2. collect_week_content — reads newsletter sections + top Track 2 news
    3. compile_newsletter — Claude assembles full HTML issue
    4. generate_subject_lines — Claude returns 3 options, use the first
    5. send_newsletter — GHL Email API; on failure, saves locally and Slack alerts
    6. save_newsletter_issue — writes HTML + markdown to newsletter/{week_start}/

    Returns: {"subject": "...", "send_result": {...}, "saved_to": "..."}
    """
    if week_start is None:
        week_start = _current_week_monday()

    logger.info(f"=== Friday newsletter pipeline starting — week of {week_start} ===")

    # 1. Collect
    week_content = collect_week_content(week_start)

    # 2. Compile
    html_body = compile_newsletter(week_content, week_start)

    # 3. Subject lines
    subject_options = generate_subject_lines(html_body)
    subject = subject_options[0] if subject_options else f"This week in AI automation — {week_start}"
    logger.info(f"Using subject: '{subject}'")
    logger.info(f"Other options: {subject_options[1:]}")

    # 4. Send
    send_result = {}
    try:
        send_result = send_newsletter(html_body, subject)
    except Exception as e:
        logger.error(f"Newsletter send failed — saving locally and alerting Slack: {e}")
        _alert_slack(
            f"⚠️ *NEWSLETTER SEND FAILED — {week_start}*\n"
            f"Error: {e}\n"
            f"Issue saved locally. Manual send required."
        )

    # 5. Save
    saved_to = save_newsletter_issue(html_body, subject, week_start)

    logger.info(f"=== Friday newsletter pipeline complete ===")

    return {
        "subject":      subject,
        "send_result":  send_result,
        "saved_to":     saved_to,
        "subject_options": subject_options,
    }


if __name__ == "__main__":
    result = run_friday_newsletter()
    print(f"Subject: {result['subject']}")
    print(f"Saved to: {result['saved_to']}")
    print(f"Send result: {result['send_result']}")
