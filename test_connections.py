"""
test_connections.py — Validates every API connection without posting or creating content.
Run: python3 test_connections.py
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

PASS = "✓"
FAIL = "✗"
SKIP = "—"
results = []

def check(name, passed, detail=""):
    status = PASS if passed else FAIL
    msg = f"  {status}  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((name, passed))

def section(title):
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
section("Anthropic")
try:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": "Say: ok"}],
    )
    check("Anthropic API", True, msg.content[0].text.strip())
except Exception as e:
    check("Anthropic API", False, str(e))


# ---------------------------------------------------------------------------
# Reddit (public JSON — no auth)
# ---------------------------------------------------------------------------
section("Reddit (public JSON)")
try:
    resp = requests.get(
        "https://www.reddit.com/r/artificial/top.json?t=day&limit=1",
        headers={"User-Agent": os.environ.get("REDDIT_USER_AGENT", "CSKContentBot/1.0")},
        timeout=10,
    )
    resp.raise_for_status()
    posts = resp.json().get("data", {}).get("children", [])
    check("Reddit public JSON", True, f"{len(posts)} post(s) returned")
except Exception as e:
    check("Reddit public JSON", False, str(e))


# ---------------------------------------------------------------------------
# YouTube Data API
# ---------------------------------------------------------------------------
section("YouTube Data API")
try:
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={"part": "snippet", "q": "AI automation", "maxResults": 1,
                "key": os.environ["YOUTUBE_API_KEY"]},
        timeout=10,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])
    check("YouTube Data API", True, f"{len(items)} result(s) returned")
except Exception as e:
    check("YouTube Data API", False, str(e))


# ---------------------------------------------------------------------------
# Twitter/X Bearer Token
# ---------------------------------------------------------------------------
section("Twitter/X")
try:
    resp = requests.get(
        "https://api.twitter.com/2/tweets/search/recent",
        params={"query": "AI automation", "max_results": 10},
        headers={"Authorization": f"Bearer {os.environ['TWITTER_BEARER_TOKEN']}"},
        timeout=10,
    )
    if resp.status_code == 200:
        count = len(resp.json().get("data", []))
        check("Twitter Bearer Token", True, f"{count} tweet(s) returned")
    elif resp.status_code == 403:
        check("Twitter Bearer Token", False, "403 — Free tier doesn't include search. Upgrade to Basic ($100/mo) or remove Twitter scraping.")
    else:
        check("Twitter Bearer Token", False, f"HTTP {resp.status_code}: {resp.text[:100]}")
except Exception as e:
    check("Twitter Bearer Token", False, str(e))


# ---------------------------------------------------------------------------
# Perplexity
# ---------------------------------------------------------------------------
section("Perplexity")
try:
    resp = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": "sonar",
            "messages": [{"role": "user", "content": "Say: ok"}],
            "max_tokens": 5,
        },
        timeout=15,
    )
    resp.raise_for_status()
    check("Perplexity API", True, resp.json()["choices"][0]["message"]["content"].strip())
except Exception as e:
    check("Perplexity API", False, str(e))


# ---------------------------------------------------------------------------
# GoHighLevel
# ---------------------------------------------------------------------------
section("GoHighLevel")
ghl_headers = {
    "Authorization": f"Bearer {os.environ['GHL_API_KEY']}",
    "Version": "2021-07-28",
    "Content-Type": "application/json",
}

# Test 1: location lookup
try:
    resp = requests.get(
        f"https://services.leadconnectorhq.com/locations/{os.environ['GHL_LOCATION_ID']}",
        headers=ghl_headers,
        timeout=10,
    )
    if resp.status_code == 200:
        name = resp.json().get("location", {}).get("name", "unknown")
        check("GHL location auth", True, name)
    else:
        check("GHL location auth", False, f"HTTP {resp.status_code}: {resp.text[:100]}")
except Exception as e:
    check("GHL location auth", False, str(e))

# Test 2: social planner accounts
try:
    resp = requests.get(
        f"https://services.leadconnectorhq.com/social-media-posting/{os.environ['GHL_LOCATION_ID']}/accounts",
        headers=ghl_headers,
        timeout=10,
    )
    if resp.status_code == 200:
        accounts = resp.json()
        platforms = [a.get("platform", "?") for a in accounts] if isinstance(accounts, list) else []
        check("GHL Social Planner accounts", True, f"platforms: {platforms or accounts}")
    else:
        check("GHL Social Planner accounts", False, f"HTTP {resp.status_code}: {resp.text[:120]}")
except Exception as e:
    check("GHL Social Planner accounts", False, str(e))


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------
section("Slack")
slack_headers = {
    "Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}",
    "Content-Type": "application/json",
}

# Test 1: auth check
try:
    resp = requests.post("https://slack.com/api/auth.test", headers=slack_headers, timeout=10)
    data = resp.json()
    if data.get("ok"):
        check("Slack bot auth", True, f"bot: {data.get('bot_id')}, workspace: {data.get('team')}")
    else:
        check("Slack bot auth", False, data.get("error", "unknown"))
except Exception as e:
    check("Slack bot auth", False, str(e))

# Test 2: channel access
for env_var, label in [
    ("SLACK_APPROVAL_CHANNEL_ID", "approval channel"),
    ("SLACK_NEWS_CHANNEL_ID", "news channel"),
    ("SLACK_PERFORMANCE_CHANNEL_ID", "performance channel"),
]:
    try:
        channel_id = os.environ[env_var]
        resp = requests.post(
            "https://slack.com/api/conversations.info",
            headers=slack_headers,
            json={"channel": channel_id},
            timeout=10,
        )
        data = resp.json()
        if data.get("ok"):
            check(f"Slack {label}", True, data["channel"].get("name", channel_id))
        else:
            check(f"Slack {label}", False, data.get("error", "unknown"))
    except Exception as e:
        check(f"Slack {label}", False, str(e))


# ---------------------------------------------------------------------------
# HeyGen
# ---------------------------------------------------------------------------
section("HeyGen")
try:
    resp = requests.get(
        "https://api.heygen.com/v2/avatars",
        headers={"X-Api-Key": os.environ["HEYGEN_API_KEY"]},
        timeout=10,
    )
    if resp.status_code == 200:
        avatars = resp.json().get("data", {}).get("avatars", [])
        avatar_ids = [a.get("avatar_id") for a in avatars[:3]]
        target = os.environ.get("HEYGEN_AVATAR_ID", "")
        found = any(a.get("avatar_id") == target for a in avatars)
        check("HeyGen API key", True, f"{len(avatars)} avatar(s) available")
        check("HeyGen avatar ID", found, target if found else f"NOT FOUND — available: {avatar_ids}")
    else:
        check("HeyGen API key", False, f"HTTP {resp.status_code}: {resp.text[:100]}")
except Exception as e:
    check("HeyGen API key", False, str(e))


# ---------------------------------------------------------------------------
# OpenAI (Whisper)
# ---------------------------------------------------------------------------
section("OpenAI")
try:
    import openai
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    models = client.models.list()
    whisper_available = any("whisper" in m.id for m in models.data)
    check("OpenAI API key", True, "whisper available" if whisper_available else "key valid but whisper not listed")
except Exception as e:
    check("OpenAI API key", False, str(e))


# ---------------------------------------------------------------------------
# Cloudinary
# ---------------------------------------------------------------------------
section("Cloudinary")
try:
    import cloudinary
    import cloudinary.api
    cloudinary.config(
        cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        api_key=os.environ["CLOUDINARY_API_KEY"],
        api_secret=os.environ["CLOUDINARY_API_SECRET"],
    )
    result = cloudinary.api.ping()
    check("Cloudinary", result.get("status") == "ok", result.get("status", str(result)))
except Exception as e:
    check("Cloudinary", False, str(e))


# ---------------------------------------------------------------------------
# YouTube OAuth (upload credentials)
# ---------------------------------------------------------------------------
section("YouTube OAuth (upload)")
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    check("YouTube OAuth refresh", True, "token refreshed successfully")
except Exception as e:
    check("YouTube OAuth refresh", False, str(e))


# ---------------------------------------------------------------------------
# Instagram Graph API
# ---------------------------------------------------------------------------
section("Instagram Graph API")
try:
    resp = requests.get(
        f"https://graph.facebook.com/v18.0/{os.environ['INSTAGRAM_USER_ID']}",
        params={
            "fields": "id,username",
            "access_token": os.environ["INSTAGRAM_ACCESS_TOKEN"],
        },
        timeout=10,
    )
    if resp.status_code == 200:
        data = resp.json()
        check("Instagram Graph API", True, f"@{data.get('username', data.get('id'))}")
    else:
        check("Instagram Graph API", False, f"HTTP {resp.status_code}: {resp.json().get('error', {}).get('message', resp.text[:100])}")
except Exception as e:
    check("Instagram Graph API", False, str(e))


# ---------------------------------------------------------------------------
# Apify
# ---------------------------------------------------------------------------
section("Apify")
try:
    from apify_client import ApifyClient
    apify_key = os.environ.get("APIFY_API_KEY", "")
    if not apify_key:
        check("Apify API key", False, "APIFY_API_KEY not set")
    else:
        client = ApifyClient(apify_key)
        user = client.user("me").get()
        username = user.get("username", "unknown")
        plan = user.get("plan", {}).get("id", "unknown")
        check("Apify API key", True, f"username: {username}, plan: {plan}")
except Exception as e:
    check("Apify API key", False, str(e))


# ---------------------------------------------------------------------------
# TikTok (scraping only — publishing is manual)
# ---------------------------------------------------------------------------
section("TikTok")
try:
    access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
    if not access_token:
        check("TikTok access token", True, "skipped — TikTok publishing is manual; scraping via Apify")
    else:
        resp = requests.get(
            "https://open.tiktokapis.com/v2/user/info/",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "open_id,display_name"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {}).get("user", {})
            check("TikTok access token", True, f"@{data.get('display_name', data.get('open_id', 'unknown'))}")
        elif resp.status_code == 401:
            check("TikTok access token", False, "Token expired — regenerate at developers.tiktok.com")
        else:
            check("TikTok access token", False, f"HTTP {resp.status_code}: {resp.text[:100]}")
except Exception as e:
    check("TikTok access token", False, str(e))


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'═' * 50}")
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f"  Results: {passed} passed, {failed} failed")
if failed:
    print(f"\n  Failed checks:")
    for name, ok in results:
        if not ok:
            print(f"    {FAIL}  {name}")
print(f"{'═' * 50}\n")
sys.exit(0 if failed == 0 else 1)
