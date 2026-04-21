# CSK Content Engine — CLAUDE.md

Automated content pipeline for CSK Tech Solutions. Runs entirely on GitHub Actions.
No UI. No framework. Plain Python scripts wired together.

## What this does

Four pipelines, all triggered by GitHub Actions cron jobs:

| Pipeline | Entry point | Schedule | What it does |
|---|---|---|---|
| Track 1 (Daily) | `main_daily.py` | Weekdays 7 AM CST | Scrape ideas → score → generate content → Slack approval → publish LinkedIn + video |
| Track 2 (News) | `main_news.py` | Every 4 hours | Scrape AI/tech news → score → auto-post (7+) or send to Slack for review (5-6) |
| Newsletter | `main_friday.py` | Fridays 7:30 AM CST | Compile weekly content into CSK Brief → send via GHL email → generate weekly hero video |
| Performance | `main_monday.py` | Mondays 8 AM CST | Pull engagement metrics → generate report → post to #content-performance |

## Architecture

```
Scrapers → Claude scoring → Content generation → Slack approval → Publishers
```

- **Scrapers**: `idea_scraper.py` (Reddit, HN, Google Trends, YouTube, Twitter/Apify, TikTok/Apify, Instagram/Apify, Quora) and `news_scraper.py` (20+ RSS feeds + HN + Reddit)
- **Claude**: `content_creator.py` — generates all formats using `claude-sonnet-4-6`; idea scoring uses `claude-opus-4-6`
- **Approval flow**: Posts to `#content-approval` in Slack with ✅ ❌ ✏️ reactions. Tracked in `pending_approvals.json`. Auto-approves after 24h (Track 1), 6h (Track 2), 48h (video)
- **Publishing**: GHL Social Planner for LinkedIn, Twitter API v2 for threads, GHL email for newsletter
- **Video pipeline**: HeyGen REST API v2 (daily 60s clips + weekly multi-scene) → Whisper captions via MoviePy → Cloudinary upload → LinkedIn + YouTube + Instagram. TikTok: operator posts manually.

## File map

```
main_daily.py          # Track 1 entry point — supports --dry-run flag
main_news.py           # Track 2 entry point
main_friday.py         # Newsletter entry point + weekly hero video generation
main_monday.py         # Performance report entry point

idea_scraper.py        # 8-source scraper: Reddit, HN, Google Trends, YouTube,
                       # Twitter (Apify), TikTok (Apify), Instagram (Apify), Quora
                       # + Claude scoring + viral style analysis
news_scraper.py        # RSS + HN + Reddit news scraper + deduplication
content_creator.py     # All Claude content generation (all formats, all tracks)
                       # Includes: LinkedIn CTA rotation, video scene manifest generator
content_publisher.py   # Slack approval, GHL LinkedIn, Twitter threads, Canva carousel
newsletter_builder.py  # Weekly newsletter compilation + GHL email send
performance_tracker.py # Metrics pull + Claude performance report generation
heygen_video.py        # HeyGen REST API v2: single-scene (daily) + multi-scene manifest (weekly)
caption_video.py       # Whisper transcription → MoviePy caption overlay
video_publisher.py     # Cloudinary upload + LinkedIn video + YouTube Shorts + Instagram Reels
                       # TikTok: scraping only (Apify); operator posts videos manually
remotion_renderer.py   # Remotion-based video rendering (alternative to HeyGen)

utils/logger.py        # Shared rotating file + console logger → content.log
utils/rate_limiter.py  # polite_delay() and api_delay() for scraper throttling

test_connections.py    # Validates every API key without creating content
fix_slack_channels.py  # Lists all Slack channel IDs (run when channel IDs are wrong)
fix_youtube_oauth.py   # Runs local OAuth flow to regenerate YouTube refresh token
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

## Brand identity

- **Primary color**: `#1A3C5E` (navy)
- **Accent color**: `#00B4D8` (teal)
- **Background**: White or light only. No dark themes on any CSK marketing assets.
- **Lead magnet**: "The AI Automation Checklist for Accounting Firms" (HTML asset)
- **Audit booking link**: [INSERT GHL calendar link]

## LinkedIn CTA rotation (mandatory on every Track 1 post)

Every LinkedIn post generated by `content_creator.py` ends with exactly one rotating CTA. Index is tracked in `pending_approvals.json` as `cta_index` and increments after each post.

```python
LINKEDIN_CTAS = [
    "I do free AI Workflow Audits for accounting firms and SMBs — 45 minutes, no pitch, just clarity on what to automate first. Link in bio to grab a spot.",
    "Built a free checklist: 10 manual processes accounting firms should have automated by now. Link in bio to download it.",
    "DM me 'AUDIT' and I'll send you the AI workflow checklist for your industry.",
    "If your team is still doing {topic} manually, we should talk. Free audit link in bio — takes 45 minutes.",
]
```

**Rules:**
- Never use the same CTA two days in a row.
- CTA goes on its own line, separated by a blank line from the post body.
- The 4th CTA dynamically replaces `{topic}` with a short phrase from the idea title.

## Scoring logic

Ideas are scored 0–10 across 4 criteria (Claude returns JSON):

| Criterion | Max | What it measures |
|---|---|---|
| `audience_relevance` | 3 | Does this directly affect our ICP? |
| `engagement_signal` | 3 | How much traction did it get? |
| `csk_angle` | 2 | Can we tie this to a CSK service? |
| `originality` | 2 | Is this already overdone on LinkedIn? |

- Score 7+: auto-post (Track 2) or generate video (Track 1 top ideas)
- Score 5–6: send to Slack for human review
- Score <5: skip

## Video pipeline — HeyGen REST API v2

All video generation uses the HeyGen REST API directly (`api.heygen.com`). No CLI required.

### Daily short clips (LinkedIn/Instagram/YouTube Shorts — 60 seconds)
`heygen_video.generate_daily_video()` submits a single-scene `POST /v2/video/generate` request with the avatar, voice, and full 60-second script. Polls `/v1/video_status.get` until completed (up to 30 min), then downloads the MP4.

Generated for all Track 1 ideas with score >= 7 (up to top 3 per day). Output: `content/{date}/track3/daily_{slug}.mp4`

### Weekly hero video (YouTube/Instagram — 2–3 minutes)
`heygen_video.generate_weekly_video()` reads the week's `video_scene_manifest.json`, builds a multi-scene `video_inputs` array (one entry per scene), and submits a single `POST /v2/video/generate` request. `main_friday.py` finds the manifest and triggers this each Friday.

Output: `content/{week}/weekly_video/weekly_hero.mp4`

### TikTok
TikTok scraping runs via Apify (`clockworks/free-tiktok-scraper`). Videos rendered by the pipeline are posted to TikTok manually by the operator — no API publishing.

### Getting correct avatar ID
```python
python3 heygen_video.py   # lists available avatars from the API → copy ID → set HEYGEN_AVATAR_ID in .env
```

## Scraping sources

| Source | Method | What it finds |
|---|---|---|
| Reddit | Public JSON (no auth) | Top posts from 10 subreddits |
| Hacker News | Firebase API | Top stories with AI/automation keywords |
| Google Trends | pytrends | Rising search terms |
| YouTube | YouTube Data API v3 | Trending videos (works) |
| Twitter/X | Apify `apidojo/tweet-scraper` | Tweets with CSK keywords (no bearer token needed) |
| TikTok | Apify `clockworks/free-tiktok-scraper` | Trending TikTok videos |
| Instagram | Apify `apify/instagram-scraper` | Trending posts by hashtag |
| Quora | Playwright headless | Questions related to automation |

## Publishing platforms

| Platform | Method | Content types |
|---|---|---|
| LinkedIn | GHL Social Planner | Text posts + video |
| Twitter/X | Twitter API v2 (Bearer Token) | Threads — token needs regeneration |
| YouTube | YouTube Data API v3 + OAuth2 | Shorts (60s), hero video |
| Instagram | Facebook Graph API v18.0 | Reels — token expires every 60 days |
| TikTok | Manual (operator posts) | Videos downloaded locally; no API publishing |
| Email | GHL Email API | Friday newsletter |

## Runtime files

These are created at runtime and git-ignored:

- `content/{date}/ideas/scraped_ideas.json` — raw scraper output
- `content/{date}/ideas/scored_ideas.json` — scored + sorted
- `content/{date}/track1/` — LinkedIn post, Twitter thread, blog post, newsletter section, carousel brief, video script, video scene manifest
- `content/{date}/track2/{story_id}/` — news content per story
- `content/{date}/track3/` — daily video MP4s and metadata
- `content/{week}/weekly_video/weekly_hero.mp4` — weekly hero video
- `pending_approvals.json` — live approval queue + `cta_index` for LinkedIn CTA rotation
- `seen_stories.json` — Track 2 deduplication store
- `content.log` — rotating log file for all pipeline activity

## Environment variables

All secrets live in `.env` locally and in GitHub Actions secrets for CI. Never commit `.env`.

| Group | Variables | Used by |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | content_creator.py, idea_scraper.py |
| Apify | `APIFY_API_KEY` | idea_scraper.py — Twitter, TikTok, Instagram scraping |
| GHL | `GHL_API_KEY`, `GHL_LOCATION_ID`, `GHL_LINKEDIN_ACCOUNT_ID`, `GHL_FROM_EMAIL`, `GHL_REPLY_TO_EMAIL` | content_publisher.py, newsletter_builder.py |
| Slack | `SLACK_BOT_TOKEN`, `SLACK_APPROVAL_CHANNEL_ID`, `SLACK_NEWS_CHANNEL_ID`, `SLACK_PERFORMANCE_CHANNEL_ID`, `SLACK_WORKSPACE_OWNER_ID` | all mains |
| HeyGen | `HEYGEN_API_KEY`, `HEYGEN_AVATAR_ID`, `HEYGEN_VOICE_ID` | heygen_video.py |
| YouTube | `YOUTUBE_API_KEY` (scraping), `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` (upload) | idea_scraper.py, video_publisher.py |
| Twitter | `TWITTER_BEARER_TOKEN` | content_publisher.py (publishing only — scraping replaced by Apify) |
| Instagram | `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN` | video_publisher.py — expires every 60 days |
| TikTok | `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET` | Reserved for future publishing — not used yet |
| Cloudinary | `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET` | video_publisher.py |
| OpenAI | `OPENAI_API_KEY` | caption_video.py (Whisper only) |
| Perplexity | `PERPLEXITY_API_KEY` | news_scraper.py |

## Known issues / token status (as of April 2026)

- **Anthropic API key**: invalid — get a new key at console.anthropic.com. This is the most critical blocker; Claude drives all content generation.
- **Perplexity API key**: invalid — get a new key at perplexity.ai/settings/api. Used by news_scraper.py for Track 2 story research.
- **Twitter Bearer Token**: returning 401 — scraping replaced by Apify (no impact). Bearer Token still needed for publishing threads; regenerate at developer.twitter.com when activating Twitter posting.
- **Slack channel IDs**: bot not invited to channels — run `/invite @YourBotName` in #content-approval, #ai-news-feed, and #content-performance in Slack.
- **YouTube refresh token**: expired — run `python3 fix_youtube_oauth.py` to regenerate.
- **Instagram access token**: expired April 2, 2026 — tokens expire every 60 days; refresh via Meta Business Manager.
- **HeyGen**: API key is valid (1,283 avatars confirmed). Avatar ID `125b953bfeff436f87db22eada0883ad` confirmed in .env.
- **TikTok publishing**: intentionally not automated — operator posts videos manually. Scraping via Apify is active.
- **Canva API key**: not set — enterprise plan required; use Canva MCP connector instead (see below).

## Local setup

```bash
git clone https://github.com/momodevlab/csk-content-engine.git
cd csk-content-engine
pip install -r requirements.txt
playwright install chromium
cp .env.example .env                # fill in your values
python3 heygen_video.py             # confirm HEYGEN_AVATAR_ID (lists avatars from API)
python3 test_connections.py         # verify all connections before running
```

## Running pipelines locally

```bash
python3 main_daily.py               # Track 1 — daily content (live)
python3 main_daily.py --dry-run     # Track 1 — generate content locally, no Slack/publish
python3 main_news.py                # Track 2 — news feed
python3 main_friday.py              # Newsletter + weekly video
python3 main_monday.py              # Performance report
```

## Utility scripts

```bash
python3 test_connections.py     # check all API keys
python3 fix_slack_channels.py   # list Slack channel IDs
python3 fix_youtube_oauth.py    # regenerate YouTube OAuth refresh token
python3 heygen_video.py         # list available HeyGen avatars from the API
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
2. Entry written to `pending_approvals.json` with `auto_approve_at` timestamp and current `cta_index`
3. GitHub Actions cron runs `check_reactions()` + `check_auto_approvals()` hourly
4. ✅ reaction → loads content from disk → publishes to platforms → updates status to "approved"
5. ❌ reaction → status set to "rejected", removed from queue
6. ✏️ reaction → DMs workspace owner with edit request, status set to "edit_requested"
7. Auto-approval fires if no human reaction by the deadline (24h Track 1, 6h Track 2, 48h video)
8. Carousels (`never_auto_approve: true`) never auto-approve

## Content output formats (per Track 1 package)

Each idea generates all of these:
- `linkedin_post.md` — full LinkedIn post (~150–300 words) with rotating CTA appended
- `twitter_thread.md` — 8–10 tweets separated by `\n\n---\n\n`
- `blog_post.md` — 800–1,200 word blog post in markdown
- `newsletter_section.md` — 2–3 paragraph newsletter blurb
- `carousel_brief.json` — slide-by-slide brief for Canva carousel generation
- `video_script.md` — 60-second spoken word script for HeyGen Video Agent (daily)
- `video_scene_manifest.json` — multi-scene manifest with avatar + b-roll prompts (top idea only, used for weekly hero video on Friday)
