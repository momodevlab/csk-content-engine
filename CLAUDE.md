# CSK Content Engine — CLAUDE.md

Automated content pipeline for CSK Tech Solutions. Runs entirely on GitHub Actions.
No UI. No framework. Plain Python scripts wired together.

## What this does

Four pipelines, all triggered by GitHub Actions cron jobs:

| Pipeline | Entry point | Schedule | What it does |
|---|---|---|---|
| Track 1 (Daily) | `main_daily.py` | Weekdays 7 AM CST | Scrape ideas → score → generate content → Slack approval → publish LinkedIn + Twitter |
| Track 2 (News) | `main_news.py` | Every 4 hours | Scrape AI/tech news → score → auto-post (7+) or send to Slack for review (5-6) |
| Newsletter | `main_friday.py` | Fridays 7:30 AM CST | Compile weekly content into CSK Brief → send via GHL email |
| Performance | `main_monday.py` | Mondays 8 AM CST | Pull engagement metrics → generate report → post to #content-performance |

## Architecture

```
Scrapers → Claude scoring → Content generation → Slack approval → Publishers
```

- **Scrapers**: `idea_scraper.py` (Reddit, HN, Google Trends, YouTube, Twitter, Quora) and `news_scraper.py` (20+ RSS feeds + HN + Reddit)
- **Claude**: `content_creator.py` — generates all formats (LinkedIn, Twitter thread, blog, newsletter section, carousel brief, video script) using `claude-sonnet-4-6`
- **Idea scoring**: Also uses Claude (`claude-opus-4-6`) to score ideas 1–10 on 4 criteria: audience_relevance, engagement_signal, csk_angle, originality
- **Approval flow**: Posts to `#content-approval` in Slack with ✅ ❌ ✏️ reactions. Tracked in `pending_approvals.json`. Auto-approves after 24h (Track 1), 6h (Track 2), 48h (video)
- **Publishing**: GHL Social Planner for LinkedIn, Twitter API v2 for threads, GHL email for newsletter
- **Video pipeline**: HeyGen avatar video → Whisper captions via MoviePy → Cloudinary upload → LinkedIn + YouTube + Instagram

## File map

```
main_daily.py          # Track 1 entry point
main_news.py           # Track 2 entry point
main_friday.py         # Newsletter entry point
main_monday.py         # Performance report entry point

idea_scraper.py        # 6-source idea scraper + Claude scoring + viral style analysis
news_scraper.py        # RSS + HN + Reddit news scraper + deduplication
content_creator.py     # All Claude content generation (all formats, all tracks)
content_publisher.py   # Slack approval, GHL LinkedIn, Twitter threads, Canva carousel
newsletter_builder.py  # Weekly newsletter compilation + GHL email send
performance_tracker.py # Metrics pull + Claude performance report generation
heygen_video.py        # HeyGen API: script → avatar video render
caption_video.py       # Whisper transcription → MoviePy caption overlay
video_publisher.py     # YouTube OAuth upload + Instagram Graph API post
remotion_renderer.py   # Remotion-based video rendering (alternative to HeyGen)

utils/logger.py        # Shared rotating file + console logger → content.log
utils/rate_limiter.py  # polite_delay() and api_delay() for scraper throttling

test_connections.py    # Validates every API key without creating content
fix_slack_channels.py  # Lists all Slack channel IDs (run when channel IDs are wrong)
fix_youtube_oauth.py   # Runs local OAuth flow to regenerate YouTube refresh token

.github/workflows/     # GitHub Actions workflow definitions (4 files)
content/               # Runtime output: ideas, content, logs (git-ignored)
newsletter/            # Newsletter drafts (git-ignored)
performance/           # Performance reports (git-ignored)
pending_approvals.json # Tracks Slack approval state across pipeline runs (runtime)
```

## Brand voice rules (baked into content_creator.py)

Claude writes content with these rules hard-coded in the system prompt:

- Direct, plain English. No fluff. Like a brilliant friend who knows tech.
- Tie everything to business outcomes and ROI — never just describe features.
- Educational first. Never promotional. Earn trust before asking for anything.
- Short sentences. Contractions fine.

**Banned words**: synergy, leverage (as verb), circle back, bandwidth (for people), cutting-edge, state-of-the-art, best-in-class, disruptive, revolutionizing, transforming, "we are passionate about", "we are committed to", solutions (standalone noun)

**Banned patterns**: em dashes for dramatic effect, parallel tricolon, "In today's...", ending with "reach out to learn more"

**Target audience** (written for these people specifically):
- Accounting firm owners/managing partners (10–50 staff)
- Insurance agency owners and ops managers
- Marketing agency founders and COOs
- CTOs/VP Eng at Seed–Series B startups (FinTech, HealthTech, SaaS, InsurTech)

## Scoring logic

Ideas are scored 0–10 across 4 criteria (Claude returns JSON):

| Criterion | Max | What it measures |
|---|---|---|
| `audience_relevance` | 3 | Does this directly affect our ICP? |
| `engagement_signal` | 3 | How much traction did it get? |
| `csk_angle` | 2 | Can we tie this to a CSK service? |
| `originality` | 2 | Is this already overdone on LinkedIn? |

- Score 7+: auto-post (Track 2) or generate video (Track 1 top idea)
- Score 5–6: send to Slack for human review
- Score <5: skip

## Runtime files

These are created at runtime and git-ignored:

- `content/{date}/ideas/scraped_ideas.json` — raw scraper output
- `content/{date}/ideas/scored_ideas.json` — scored + sorted
- `content/{date}/track1/` — LinkedIn post, Twitter thread, blog post, newsletter section, carousel brief
- `content/{date}/track2/{story_id}/` — news content per story
- `pending_approvals.json` — live approval queue (read/written by multiple pipeline steps)
- `seen_stories.json` — Track 2 deduplication store
- `content.log` — rotating log file for all pipeline activity

## Environment variables

All secrets live in `.env` locally and in GitHub Actions secrets for CI. Never commit `.env`.

See `.env.example` for the full list. Key groups:

| Group | Variables | Used by |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | content_creator.py, idea_scraper.py |
| GHL | `GHL_API_KEY`, `GHL_LOCATION_ID`, `GHL_LINKEDIN_ACCOUNT_ID`, `GHL_FROM_EMAIL`, `GHL_REPLY_TO_EMAIL` | content_publisher.py, newsletter_builder.py |
| Slack | `SLACK_BOT_TOKEN`, `SLACK_APPROVAL_CHANNEL_ID`, `SLACK_NEWS_CHANNEL_ID`, `SLACK_PERFORMANCE_CHANNEL_ID`, `SLACK_WORKSPACE_OWNER_ID` | all mains |
| HeyGen | `HEYGEN_API_KEY`, `HEYGEN_AVATAR_ID`, `HEYGEN_VOICE_ID` | heygen_video.py |
| YouTube | `YOUTUBE_API_KEY` (scraping), `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` (upload) | idea_scraper.py, video_publisher.py |
| Twitter | `TWITTER_BEARER_TOKEN` | idea_scraper.py, content_publisher.py |
| Instagram | `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN` | video_publisher.py |
| Cloudinary | `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | video_publisher.py |
| OpenAI | `OPENAI_API_KEY` | caption_video.py (Whisper only) |
| Perplexity | `PERPLEXITY_API_KEY` | news_scraper.py |

## Known issues / token status (as of April 2026)

- **Twitter Bearer Token**: returning 401 — needs regeneration at developer.twitter.com
- **HeyGen Avatar ID**: `HEYGEN_AVATAR_ID` in `.env` does not match any avatar — update with correct ID from HeyGen dashboard
- **YouTube refresh token**: expired — run `python3 fix_youtube_oauth.py` to regenerate
- **Instagram access token**: expired April 2, 2026 — long-lived tokens expire every 60 days, refresh via Meta Business Manager
- **Canva API key**: not set — enterprise plan required for API key; use Canva MCP connector instead (see MCP section below)

## Local setup

```bash
git clone https://github.com/momodevlab/csk-content-engine.git
cd csk-content-engine
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # fill in your values
python3 test_connections.py  # verify all connections before running
```

## Running pipelines locally

```bash
python3 main_daily.py     # Track 1 — daily content
python3 main_news.py      # Track 2 — news feed
python3 main_friday.py    # Newsletter
python3 main_monday.py    # Performance report
```

## Utility scripts

```bash
python3 test_connections.py     # check all API keys
python3 fix_slack_channels.py   # list Slack channel IDs
python3 fix_youtube_oauth.py    # regenerate YouTube OAuth refresh token
```

## GitHub Actions secrets

Before the workflows will run on GitHub, add every variable from `.env` as a repository secret at:
`Settings → Secrets and variables → Actions → New repository secret`

The workflow files in `.github/workflows/` reference them as `${{ secrets.VARIABLE_NAME }}`.

## Canva MCP (alternative to API key)

The Canva API key requires an enterprise plan. Instead, configure the Canva MCP connector in Claude Code:

Add to `.mcp.json` at the project root:
```json
{
  "mcpServers": {
    "canva": {
      "command": "npx",
      "args": ["-y", "@canva/mcp"]
    }
  }
}
```

First run will prompt for Canva OAuth login. No enterprise plan needed.
The `generate_canva_carousel()` function in `content_publisher.py` will need to be updated to use the MCP instead of the REST API if you go this route.

## Approval flow detail

1. Pipeline posts to `#content-approval` with ✅ ❌ ✏️ reactions pre-added
2. Entry written to `pending_approvals.json` with `auto_approve_at` timestamp
3. GitHub Actions cron runs `check_reactions()` + `check_auto_approvals()` hourly
4. ✅ reaction → loads content from disk → publishes to platforms → updates status to "approved"
5. ❌ reaction → status set to "rejected", removed from queue
6. ✏️ reaction → DMs workspace owner with edit request, status set to "edit_requested"
7. Auto-approval fires if no human reaction by the deadline (24h Track 1, 6h Track 2, 48h video)
8. Carousels (`never_auto_approve: true`) never auto-approve

## Content output formats (per Track 1 package)

Each idea generates all of these:
- `linkedin_post.md` — full LinkedIn post (~1200–1800 chars)
- `twitter_thread.md` — 4–6 tweets separated by `\n\n---\n\n`
- `blog_post.md` — 800–1200 word blog post
- `newsletter_section.md` — 2–3 paragraph newsletter blurb
- `carousel_brief.json` — slide-by-slide brief for Canva carousel generation
- `video_script.md` — 60-second spoken word script for HeyGen avatar
