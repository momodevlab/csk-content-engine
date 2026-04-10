"""Rate limiting utility — prevents scraper bans."""
import time
import random

def polite_delay(min_seconds: float = 2.0, max_seconds: float = 4.0):
    """Sleep a random amount between min and max. Use between all scraper requests."""
    time.sleep(random.uniform(min_seconds, max_seconds))

def api_delay():
    """Shorter delay for API calls (not scraping)."""
    time.sleep(random.uniform(0.5, 1.5))
