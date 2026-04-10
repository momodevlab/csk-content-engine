# CSK Tech Solutions — Content Engine

Automated content pipeline for CSK Tech Solutions. Runs on GitHub Actions.

## What it does

- **Track 1 (Daily):** Scrapes trending topics → generates LinkedIn strategic posts in CSK's brand voice → Slack approval → publishes via GHL
- **Track 2 (Daily):** Scrapes AI/tech news → generates LinkedIn posts and Twitter/X threads → Slack approval → publishes
- **Friday:** Compiles the week into the CSK Brief newsletter → sends via GHL Email API at 8 AM CST
- **Monday:** Pulls engagement metrics → generates performance report → posts to Slack #content-performance at 8 AM CST

## Project structure

```
csk-content-engine/
├── content/                  # Generated content output (runtime, git-ignored)
├── newsletter/               # Newsletter drafts (runtime, git-ignored)
├── performance/              # Performance reports (runtime, git-ignored)
├── idea_scraper.py           # Reddit, YouTube, Twitter trend scraping
├── news_scraper.py           # AI/tech RSS and news scraping
├── content_creator.py        # Claude (Anthropic) content generation
├── content_publisher.py      # GHL + Twitter publishing + Slack approval
├── newsletter_builder.py     # Newsletter compilation and GHL send
├── performance_tracker.py    # Metrics pull and report generation
├── heygen_video.py           # HeyGen AI avatar video generation
├── caption_video.py          # Whisper transcription + MoviePy captions
├── video_publisher.py        # YouTube + Instagram upload
├── main_daily.py             # Track 1 pipeline entry point
├── main_news.py              # Track 2 pipeline entry point
├── main_friday.py            # Newsletter pipeline entry point
├── main_monday.py            # Performance report entry point
├── utils/
│   ├── logger.py             # Shared logging
│   └── rate_limiter.py       # Scraper rate limiting
└── .github/workflows/        # GitHub Actions workflow definitions
```

## Setup

1. Copy `.env.example` to `.env` and fill in all values
2. Install dependencies: `pip install -r requirements.txt`
3. Install Playwright browser: `playwright install chromium`
4. Add all `.env` values as GitHub Actions secrets before deploying

## Pipelines

| Workflow | File | Schedule |
|---|---|---|
| Daily content (Track 1) | `daily_content.yml` | Weekdays |
| AI news feed (Track 2) | `news_feed.yml` | Daily |
| Weekly newsletter | `weekly_newsletter.yml` | Fridays 7:30 AM CST |
| Weekly report | `weekly_report.yml` | Mondays 8:00 AM CST |
