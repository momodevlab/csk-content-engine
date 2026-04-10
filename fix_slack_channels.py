"""
fix_slack_channels.py — List all Slack channels your bot can see and print their IDs.

Run:  python3 fix_slack_channels.py
Then copy the correct IDs into your .env file.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.environ["SLACK_BOT_TOKEN"]
headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

print("\nFetching channels your bot has access to...\n")

channels = []
cursor = None

while True:
    params = {"limit": 200, "exclude_archived": True, "types": "public_channel,private_channel"}
    if cursor:
        params["cursor"] = cursor

    resp = requests.post(
        "https://slack.com/api/conversations.list",
        headers=headers,
        json=params,
        timeout=15,
    )
    data = resp.json()

    if not data.get("ok"):
        print(f"Error: {data.get('error')}")
        print("Make sure your bot has the 'channels:read' and 'groups:read' scopes.")
        break

    channels.extend(data.get("channels", []))
    cursor = data.get("response_metadata", {}).get("next_cursor")
    if not cursor:
        break

if channels:
    print(f"{'Channel Name':<35} {'ID':<15} {'Type'}")
    print("-" * 65)
    for ch in sorted(channels, key=lambda c: c.get("name", "")):
        ch_type = "private" if ch.get("is_private") else "public"
        print(f"#{ch['name']:<34} {ch['id']:<15} {ch_type}")

    print(f"\nTotal: {len(channels)} channel(s)\n")
    print("Copy the IDs for your three channels into .env:")
    print("  SLACK_APPROVAL_CHANNEL_ID=C...")
    print("  SLACK_NEWS_CHANNEL_ID=C...")
    print("  SLACK_PERFORMANCE_CHANNEL_ID=C...\n")
    print("If you don't see a channel, invite the bot first:")
    print("  /invite @<your-bot-name>  (in Slack)\n")
else:
    print("No channels found. Make sure the bot is invited to at least one channel.")
