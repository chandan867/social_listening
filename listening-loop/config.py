"""Editable seed lists. Keep phrasing US-small-biz."""
import os
from pathlib import Path

# Load environment variables from .env files if present
_env_paths = [
    Path(__file__).parent / ".env",
    Path(__file__).parent.parent / ".env",
    Path(__file__).parent.parent / "Social-ops" / ".env",
]
for _ep in _env_paths:
    if _ep.exists():
        try:
            for _line in _ep.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip("'\"")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
        except Exception:
            pass

# ponytail: flat lists + weights, no class hierarchy. Add embeddings path if keyword precision too low.

# R1 — Lead feed: owner watering holes. Trend feed: practitioner rooms (daily.py only).
LEAD_SUBREDDITS = "sweatystartup,smallbusiness,Entrepreneur,Contractor,Roofing,HVAC,solar,recruiting"
TREND_SUBREDDITS = "webdev,web_design,agency,automation,artificial,SaaS"
SUBREDDITS = LEAD_SUBREDDITS  # compat — digest.py reads SUBREDDITS

# (phrase, weight 1-30). Score capped at 100.
WEBSITE_KEYWORDS = [
    ("need a website", 28),
    ("need website", 22),
    ("website redesign", 26),
    ("redesign my site", 24),
    ("my site is slow", 22),
    ("site is slow", 18),
    ("web developer disappeared", 30),
    ("developer ghosted", 26),
    ("landing page conversion", 20),
    ("landing page not converting", 24),
    ("need a landing page", 20),
    ("small business website", 16),
    ("website for my business", 20),
    ("looking for web designer", 28),
    ("looking for web developer", 26),
    ("hire web designer", 26),
    ("hire web developer", 26),
    ("wordpress help", 14),
    ("shopify help", 14),
    ("squarespace help", 12),
    ("wix help", 12),
    ("website quote", 18),
    ("how much for a website", 22),
    ("site looks outdated", 18),
    ("mobile friendly site", 14),
    ("seo help", 14),
    ("seo fulfillment", 18),
    ("seo services", 16),
    ("need seo", 18),
    ("seo agency", 16),
    ("seo for my", 16),
    ("need booking site", 18),
    ("need ecommerce site", 20),
    ("hire developer", 22),
    ("website design", 18),
    ("web development", 18),
]

AI_KEYWORDS = [
    ("automate my business", 28),
    ("manual data entry", 22),
    ("tired of manual", 18),
    ("ai workflow", 24),
    ("ai automation", 22),
    ("zapier alternative", 20),
    ("make.com alternative", 18),
    ("chatbot for business", 24),
    ("need a chatbot", 20),
    ("automate invoicing", 20),
    ("automate lead", 18),
    ("automate follow up", 18),
    ("ai for small business", 20),
    ("ai agent for business", 18),
    ("repetitive tasks", 14),
    ("automate scheduling", 16),
    ("automate email", 14),
    ("ai receptionist", 18),
    ("voice agent", 16),
    ("custom software", 26),
    ("software for my", 20),
    ("claude setup", 26),
    ("ai agent setup", 26),
    ("hire developer", 22),
    ("automation for my", 20),
]

# All keywords merged for topic fallback; keep lowercase match.
ALL_KEYWORDS = WEBSITE_KEYWORDS + AI_KEYWORDS

VERTICAL_TERMS = ["roofing","roofer","solar","hvac","painting contractor","painter","recruiting agency","recruitment agency","staffing agency","recruiter"]

# Facebook / X search mirrors the above — comma-joined for Apify/xreach.
FACEBOOK_SEARCH = "roofing business leads, hvac company website, recruiting agency software, painting contractor marketing"
# R1 FR-1.1 — vertical-first, owner-voice; service-first demoted. One FB search per cycle, rotated.
FACEBOOK_QUERIES = [
    "roofing business slow season leads",
    "hvac company website customers",
    "recruiting agency ATS spreadsheet",
    "painting business get more jobs",
    "solar sales leads cost",
    "roofing marketing help",
    "hvac business automate scheduling",
    "painting contractor website cost",
    "recruiting agency website help",
    "small business website help",
]
X_SEARCH = "roofing business OR hvac business OR painting contractor OR recruiting agency lang:en"

# Rotated intent queries for opencli (one per platform per cycle) — vertical-first
REDDIT_QUERIES = [
    "roofing business website help",
    "hvac business leads",
    "painting contractor marketing",
    "solar business automation",
    "recruiting agency software",
    "small business website slow",
    "need website for my business roofing",
    "automate my business small",
]
TWITTER_QUERIES = [
    "roofing business website",
    "hvac business leads",
    "painting contractor help",
    "recruiting agency software",
    "solar business marketing",
    "small business automate",
]

YOUTUBE_CHANNELS = ["@MattWolfe", "@AIExplained", "@NateBJones", "@SearchEngineLand", "@SimplifiedSEO"]
# Trend feed: what's happening in the domains my audience cares about (NOT intent/lead queries)
TREND_TWITTER_QUERIES = [
    "LLM release OR model launch",
    "AI agents business",
    "Claude Code tips",
    "SEO update Google",
    "AI automation agency",
]
# ponytail: handles are plausible; existence not verified — failures skipped gracefully

# R2 intent classifier knobs
INTENT_LLM_ENABLED = os.getenv("INTENT_LLM_ENABLED", "true").lower() in ("1", "true", "yes")
INTENT_BATCH_SIZE = int(os.getenv("INTENT_BATCH_SIZE", "16"))
INTENT_CONF_THRESHOLD = float(os.getenv("INTENT_CONF_THRESHOLD", "0.6"))
INTENT_MIN_SCORE_FOR_LLM = int(os.getenv("INTENT_MIN_SCORE_FOR_LLM", "10"))  # keyword score >= this OR vertical hit -> LLM classifies

# Collection knobs (free-tier friendly)
# Pacing: look like a human, not a scraper. Every delay gets +/- jitter.
PACING = {
    "reddit_feed_delay_sec": int(os.getenv("PACING_REDDIT_FEED_DELAY_SEC", "15")),
    "opencli_delay_sec": int(os.getenv("PACING_OPENCLI_DELAY_SEC", "15")),
    "retry_backoff_sec": int(os.getenv("PACING_RETRY_BACKOFF_SEC", "30")),
    "max_retries": int(os.getenv("PACING_MAX_RETRIES", "2")),
    "jitter_sec": int(os.getenv("PACING_JITTER_SEC", "5")),
    "subs_per_cycle": int(os.getenv("PACING_SUBS_PER_CYCLE", "4")),
}
# Local free LLM for daily brief summarization + intent classification (CLIProxyAPI gateway or OpenAI-compatible)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8317/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "devtoken")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.7-flash-high")
REDDIT_RSS = os.getenv("REDDIT_RSS", "true").lower() in ("1", "true", "yes")
REDDIT_MAX_POSTS_PER_SUB = int(os.getenv("REDDIT_MAX_POSTS_PER_SUB", "50"))
REDDIT_WINDOW = os.getenv("REDDIT_WINDOW", "30d")
X_MAX_POSTS = int(os.getenv("X_MAX_POSTS", "50"))
FB_MAX_POSTS = int(os.getenv("FB_MAX_POSTS", "50"))
MCP_BASE = os.getenv("MCP_BASE", "http://localhost:8097")
MCP_TIMEOUT_SEC = int(os.getenv("MCP_TIMEOUT_SEC", "12"))
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "15"))
POLL_MAX_WAIT_SEC = int(os.getenv("POLL_MAX_WAIT_SEC", "540"))
