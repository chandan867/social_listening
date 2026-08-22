# Social Listening & Lead Intelligence Engine

Automated social listening, keyword & LLM intent scoring, and lead dispatch engine for local service businesses and agencies.

Monitors Reddit (RSS + API), Twitter/X, Facebook, and YouTube to extract high-intent leads, synthesize daily content briefs, and deliver alerts directly to Telegram.

---

## System Architecture

```text
social_listening/
├── listening-loop/           # Core Python automation loop & scoring engine
│   ├── config.py             # Seed keywords, subreddits, queries, LLM & pacing knobs
│   ├── digest.py             # 3-hour cycle: fetch -> score -> filter -> digest & leads.csv
│   ├── daily.py              # 21:00 daily brief & LinkedIn/Twitter/Reddit angle generator
│   ├── weekly.py             # Weekly pain-theme clustering & ICP review
│   ├── telegram.py           # Telegram bot notification sender
│   ├── install-scheduler.sh  # Dynamic launchd (macOS) / cron (Linux) scheduler setup
│   └── data/                 # Output digests, golden test set, and deduplication state
│
├── Social-ops/               # [Submodule] FastMCP data collection backend (:8097) & Console (:8088)
├── snscrape/                 # [Submodule/Library] Twitter/social fallback scraper
├── .env.example              # Unified environment configuration template
└── README.md                 # Team onboarding documentation
```

---

## Prerequisites

- **Python 3.10+** (stdlib-only design; no complex dependencies required)
- **Docker & Docker Desktop** (to run the `Social-ops` collector service)
- **opencli** (optional, for browser-session Reddit/Twitter/Facebook search):
  ```bash
  npm install -g opencli
  ```
- **yt-dlp** (optional, for YouTube transcript parsing in daily briefs):
  ```bash
  brew install yt-dlp   # macOS
  # or pip install yt-dlp
  ```

---

## 5-Minute Quickstart

### 1. Clone the Repository (with Submodules)

```bash
git clone --recurse-submodules <REPO_URL>
cd social_listening
```
*(If already cloned without submodules, run `git submodule update --init --recursive`)*

### 2. Configure Environment

Copy the example configuration to `.env`:

```bash
cp .env.example .env
```

Configure your credentials:
1. **Telegram:** Message [@BotFather](https://t.me/BotFather) for `TELEGRAM_BOT_TOKEN`, and [@userinfobot](https://t.me/userinfobot) for `TELEGRAM_CHAT_ID`.
2. **Reddit API (free):** Create a script app at [Reddit App Preferences](https://www.reddit.com/prefs/apps) and set `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`.
3. **Apify (optional):** Add `APIFY_API_TOKEN` for Facebook / fallback scrapers.

### 3. Start Data Collector Service (Docker)

```bash
cd Social-ops
cp ../.env .env
docker compose up -d
docker compose ps
cd ..
```
Verify `social-ops-mcp` is running on `http://localhost:8097` and setup console on `http://localhost:8088`.

### 4. Test the Pipeline

Run self-test and dry-run (safe to run without live credentials):

```bash
cd listening-loop

# 1. Run scoring & golden fixture self-test
python3 digest.py --self-test

# 2. Run a dry-run digest (writes data/digest.md and data/leads.csv)
python3 digest.py --dry-run

# 3. Test daily content brief generator
python3 daily.py --dry-run

# 4. Run live one-cycle pass
bash run.sh
```

---

## Scheduling Automation

To run the 3-hour digest and the 21:00 daily brief automatically:

### macOS (Launchd) / Linux (Cron)
Run the auto-installer script from inside `listening-loop`:

```bash
cd listening-loop
bash install-scheduler.sh
```

- **macOS:** Automatically creates and loads `~/Library/LaunchAgents/com.social-listening.digest.plist` and `com.social-listening.daily.plist` with dynamic system paths.
- **Linux:** Outputs the exact crontab entries to paste into `crontab -e`.

Logs are written to `listening-loop/data/launchd.log` and `listening-loop/data/daily.log`.

---

## Customizing Target Audience & Keywords

All vertical search terms, intent phrases, and subreddits are defined in `listening-loop/config.py`:

- `LEAD_SUBREDDITS`: Communities where business owners discuss problems (e.g. `r/sweatystartup`, `r/Roofing`, `r/HVAC`).
- `WEBSITE_KEYWORDS` & `AI_KEYWORDS`: Weighted intent terms (e.g. *"web developer disappeared"*, *"need a website"*, *"automate my business"*).
- `FACEBOOK_QUERIES` & `TWITTER_QUERIES`: Rotated intent queries.
- `LLM_BASE_URL` & `LLM_MODEL`: LLM endpoint for semantic classification and angle generation.

---

## Submodule Management for Maintainers

If submodules need updating or re-syncing:

```bash
# Pull latest submodule updates
git submodule update --remote --merge

# Commit submodule pointer changes
git add Social-ops snscrape
git commit -m "chore: update submodules"
```
