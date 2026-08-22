# Setup — copy-paste

## What's automated

- **Every 3h (digest):** Reddit RSS (8 subs) + one rotated opencli search per platform per cycle (Reddit/Twitter/Facebook) → keyword scoring → `data/digest.md` + `data/leads.csv` (qualified: score ≥25 + intent hit) → Telegram (markdown + CSV document).
- **Daily 21:00 (daily brief):** `data/capture.jsonl` (appended each cycle) → top 10 topics + per-platform sections (Reddit/Twitter/Facebook/YouTube) + YouTube transcript hits via yt-dlp → Telegram markdown. No LLM — deterministic.
- **opencli channels:** `facebook` ✅ `reddit` ✅ `twitter` ✅ must be logged in. Each `opencli <site> --help` shows available commands; outputs parsed defensively. One opencli call per platform per cycle, 15s+ jitter, backoff on 429/401.

## 1) Reddit (free)

1. Go to https://www.reddit.com/prefs/apps → **create app** → type `script`, name `social-ops`, redirect `http://localhost:8080`
2. Note **client id** (under name) and **secret**
3. Edit `Social-ops/.env`:
```
REDDIT_CLIENT_ID=<id>
REDDIT_CLIENT_SECRET=<secret>
REDDIT_USER_AGENT=social-ops/0.1
```

## 2) Apify (free $5/mo credit, for Facebook 1×/day fallback)

1. https://console.apify.com/account/integrations → copy **API token**
2. In `Social-ops/.env` set:
```
APIFY_API_TOKEN=<token>
```
Facebook uses `powerai~facebook-post-search-scraper` under the hood — no extra config. Primary path is now opencli facebook search (logged-in Chrome); Apify is fallback.

## 2b) opencli login (required for Reddit/Twitter/Facebook searches)

```bash
~/.npm-global/bin/opencli reddit login
~/.npm-global/bin/opencli twitter login
~/.npm-global/bin/opencli facebook login
# verify:
~/.npm-global/bin/opencli reddit --help
~/.npm-global/bin/opencli twitter --help
~/.npm-global/bin/opencli facebook --help
```

## 3) Telegram bot

1. In Telegram, message **@BotFather** → `/newbot` → pick name → copy **token**
2. Get your **chat_id**: message **@userinfobot** (or add bot to channel and use **@getidsbot**)
3. Create `listening-loop/.env`:
```
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
```

## 4) Start Social-ops

```bash
cd Social-ops
cp .env.example .env   # if not already, then fill keys above
docker compose up -d
docker compose ps      # expect social-ops-mcp :8097 healthy, social-ops-console :8088 healthy
open http://localhost:8088   # wizard should show Providers green
curl -s http://localhost:8097/health | head
```

If Docker won't start (macOS): open **Docker Desktop** → wait for green, then retry `docker compose up -d`.

## 5) Test one cycle (no creds also works — degraded)

```bash
cd listening-loop
python3 digest.py --self-test
python3 digest.py --dry-run   # always succeeds, warns, writes data/digest.md + data/leads.csv
cat data/digest.md
python3 telegram.py           # sends digest + CSV; "0 qualified leads" msg if CSV empty
# daily dry-run (no network for youtube):
python3 daily.py --dry-run
# live (needs Telegram .env; opencli channels optional — RSS + scoring still works):
bash run.sh
# force Facebook daily outside its calendar-day window:
python3 digest.py --facebook && python3 telegram.py
```

## 6) Schedule every 3 hours + daily 21:00

**One-command scheduler setup (macOS launchd or Linux cron info):**
```bash
cd listening-loop
bash install-scheduler.sh
```

**Manual macOS (launchd):**
```bash
# Generate dynamically or load via install-scheduler.sh
# Logs:
tail -f data/launchd.log
tail -f data/daily.log
# To unload:
# launchctl unload ~/Library/LaunchAgents/com.social-listening.digest.plist
# launchctl unload ~/Library/LaunchAgents/com.social-listening.daily.plist
```

**Manual Linux/cron alternative:**
```cron
0 */3 * * * /bin/bash /path/to/listening-loop/run.sh >> /path/to/listening-loop/data/cron.log 2>&1
0 21 * * * /usr/bin/python3 /path/to/listening-loop/daily.py >> /path/to/listening-loop/data/daily.log 2>&1
```
Facebook runs once per calendar day automatically (flag `data/.fb_last`); other platforms every cycle (one rotated opencli query each).

## Troubleshooting

- **MCP not reachable / no digest content**: `docker compose ps` must show **healthy** on both services. Without it, `digest.py` still renders a degraded digest with warnings (never crashes).
- **No Facebook posts**: Apify free credit is per-month; check https://console.apify.com/actor-runs. Primary facebook path is opencli (needs `opencli facebook login`).
- **Telegram parse error**: `telegram.py` auto-retries without Markdown; check token/chat_id with `curl https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getMe`
- **X empty**: Primary is `opencli twitter search` (needs `opencli twitter login`). Social-ops `xreach + X_AUTH_TOKEN / X_CT0` is fallback.
- **YouTube empty**: `yt-dlp` at `.venv-ar/bin/yt-dlp` or system `yt-dlp`; channels in `config.YOUTUBE_CHANNELS` — failures skipped gracefully, brief still renders.
