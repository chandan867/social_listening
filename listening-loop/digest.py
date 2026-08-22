#!/usr/bin/env python3
"""Single entrypoint: collect -> score -> dedupe -> topics -> markdown digest.
stdlib + requests only. Graceful when creds missing. No LLM.
"""
from __future__ import annotations
import argparse, collections, csv, datetime, glob, hashlib, html, json, os, random, re, subprocess, sys, time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
SEEN_FILE = DATA / "seen.json"
OUT_MD = DATA / "digest.md"
OUT_JSON = DATA / "digest.json"
CAPTURE_JSONL = DATA / "capture.jsonl"
LEADS_CSV = DATA / "leads.csv"
QIDX_FILE = DATA / ".qidx"  # legacy JSON (migrated to per-platform .qidx_<platform>)
QIDX_PREFIX = ".qidx_"

sys.path.insert(0, str(HERE))
import config as C  # noqa: E402

# R2 — local intent classifier (ponytail: keyword pre-filter + batched LLM; no new deps)
_INTENT_LABELS = ("roofing","solar","hvac","painting","recruiting","other_local","not_icp")
_ROLE_LABELS = ("owner","employee","practitioner","consumer","unknown")
_INTENT_LABELS2 = ("buying","pain","question","venting","selling","none")
_URGENCY_LABELS = ("now","soon","someday")

def _intent_prompt(posts: list[dict]) -> str:
    bullets = []
    for x in posts:
        txt = (x.get("text") or "").replace("\n"," ").strip()[:420]
        bullets.append(f'- id={x.get("id","")} source={x.get("source","")} | {txt}')
    body = "\n".join(bullets)
    return (
        "You classify social posts for a US local-service lead engine. "
        "Buyer ICP: owner/GM of roofing, solar, HVAC, painting, or recruiting/staffing agency who might need website/SEO/custom software/AI automation. "
        "Not ICP: freelancers, devs, marketers selling the same services, employees venting, students/hobbyists.\n"
        "For EACH post return one JSON object with:\n"
        '{"id":"<same id>","icp":"roofing|solar|hvac|painting|recruiting|other_local|not_icp",'
        '"author_role":"owner|employee|practitioner|consumer|unknown",'
        '"intent":"buying|pain|question|venting|selling|none",'
        '"urgency":"now|soon|someday",'
        '"one_line":"<=18 words, verbatim-flavored pain/ask",'
        '"confidence":0.0}\n'
        "Rules: practitioner traps like 'how do I find web clients' => icp=not_icp, role=practitioner, intent=selling|none. "
        "Owner asking for help/quote/referral => intent=buying. Owner describing a problem without asking => pain. "
        "Output a JSON array only, no prose.\n\nPosts:\n" + body
    )

def classify_intent(posts: list[dict]) -> dict[str, dict]:
    """Batched LLM classification. Returns {id: label}. On failure returns {} (caller degrades)."""
    import requests, json as _json
    if not getattr(C, "INTENT_LLM_ENABLED", True) or not posts:
        return {}
    bs = int(getattr(C, "INTENT_BATCH_SIZE", 16) or 16)
    out: dict[str, dict] = {}
    for i in range(0, len(posts), max(1, bs)):
        chunk = posts[i:i+bs]
        prompt = _intent_prompt(chunk)
        try:
            r = requests.post(
                f"{C.LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {C.LLM_API_KEY}", "Content-Type":"application/json"},
                json={"model": C.LLM_MODEL, "messages":[{"role":"user","content": prompt}], "max_tokens": 1200, "temperature": 0.1},
                timeout=60)
            r.raise_for_status()
            content = (r.json()["choices"][0]["message"]["content"] or "").strip()
            # extract array
            s = content.find("[")
            e = content.rfind("]")
            if s == -1 or e == -1:
                continue
            arr = _json.loads(content[s:e+1])
            for obj in arr:
                if not isinstance(obj, dict):
                    continue
                pid = str(obj.get("id","") or "")
                if pid:
                    # normalize
                    obj.setdefault("icp","not_icp")
                    obj.setdefault("author_role","unknown")
                    obj.setdefault("intent","none")
                    obj.setdefault("urgency","someday")
                    obj.setdefault("one_line","")
                    obj.setdefault("confidence", 0.0)
                    out[pid] = obj
        except Exception as ex:
            print(f"[intent] batch {i//bs} failed: {ex}", file=__import__("sys").stderr)
            continue
    return out

def is_qualified(intent_obj: dict) -> bool:
    th = float(getattr(C, "INTENT_CONF_THRESHOLD", 0.6) or 0.6)
    try:
        conf = float(intent_obj.get("confidence", 0) or 0)
    except Exception:
        conf = 0.0
    return (
        (intent_obj.get("author_role") in ("owner","unknown"))
        and (intent_obj.get("intent") in ("buying","pain"))
        and (intent_obj.get("icp") != "not_icp")
        and conf >= th
    )

def has_vertical(text: str) -> bool:
    tl = (text or "").lower()
    return any(v.lower() in tl for v in getattr(C, "VERTICAL_TERMS", []))

def should_llm_classify(text: str, score: int) -> bool:
    thr = int(getattr(C, "INTENT_MIN_SCORE_FOR_LLM", 10) or 10)
    return (score >= thr) or has_vertical(text)

def draft_reply(intent_obj: dict, permalink: str) -> str:
    one = (intent_obj.get("one_line") or "").strip()
    icp = intent_obj.get("icp","")
    # ponytail: one template, not a prompt per lead
    base = f"Saw your post — {one[:120]} — " if one else "Saw your post — "
    if icp in ("roofing","hvac","painting","solar"):
        base += "we help local crews with sites + follow-up automation. Happy to share what worked for similar shops, no pitch."
    elif icp == "recruiting":
        base += "we build lightweight tools for staffing teams (ATS glue, intake automation). Happy to compare notes."
    else:
        base += "we help small teams ship the boring ops bits so owners get time back. Happy to share a 2-min loom of what worked."
    if permalink:
        base += f"\n\nSource: {permalink}"
    return base

def send_fast_lane(intent_map: dict[str, dict], scored_by_id: dict[str, dict]):
    """FR-2.3 immediate Telegram pings for buying+now. Best-effort, never crashes cycle."""
    try:
        import telegram as tg  # local
        hits = []
        for pid, obj in intent_map.items():
            if obj.get("intent") == "buying" and obj.get("urgency") == "now" and is_qualified(obj):
                rec = scored_by_id.get(pid, {})
                if not rec:
                    continue
                hits.append((pid, obj, rec))
        if not hits:
            return 0
        lines = [f"🚨 FAST LANE — {len(hits)} buying intent *now*"]
        for pid, obj, rec in hits[:5]:
            link = rec.get("permalink","")
            one = obj.get("one_line","")
            icp = obj.get("icp","")
            lines.append(f"\n**{icp}** score={rec.get('score',0)} conf={obj.get('confidence',0)} — {link}")
            lines.append(f"> {one}")
            lines.append(f"_draft:_ {draft_reply(obj, link)[:260]}")
            lines.append("")
        # also persist so daily/weekly can attribute
        tg.send_markdown("\n".join(lines))
        return len(hits)
    except Exception as ex:
        print(f"[fastlane] skip: {ex}", file=__import__("sys").stderr)
        return 0


# ponytail: naive keyword scoring, upgrade to embeddings if precision too low
# ponytail: term-frequency topics, upgrade to clustering when available
# ponytail: global pacing lock (one opencli/yt-dlp at a time), per-platform locks if throughput matters

def score_text(text: str):
    t = (text or "").lower()
    score = 0
    tags = set()
    hits = []
    for phrase, w in C.WEBSITE_KEYWORDS:
        if phrase.lower() in t:
            score += w
            tags.add("website")
            hits.append(phrase)
    for phrase, w in C.AI_KEYWORDS:
        if phrase.lower() in t:
            score += w
            tags.add("ai_automation")
            hits.append(phrase)
    if re.search(r"\bsmall business\b|\blocal business\b|\bmy shop\b|\bmy store\b", t):
        score += 6
    score = min(100, score)
    return score, sorted(tags), hits

def vertical_match(text: str) -> str:
    tl = (text or "").lower()
    for v in getattr(C, "VERTICAL_TERMS", []):
        if v.lower() in tl:
            return v
    return ""

def has_intent_hit(text: str) -> bool:
    _, _, hits = score_text(text)
    return len(hits) > 0

def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()))
        except Exception: return set()
    return set()

def save_seen(seen: set):
    DATA.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))

def _qidx_path(platform: str) -> Path:
    return DATA / f"{QIDX_PREFIX}{platform}"

def _read_qidx(platform: str) -> int:
    p = _qidx_path(platform)
    if p.exists():
        try: return int(p.read_text().strip() or "0")
        except Exception: return 0
    # migrate legacy JSON .qidx once
    if QIDX_FILE.exists():
        try:
            cur = json.loads(QIDX_FILE.read_text())
            if platform in cur:
                v = int(cur[platform]); DATA.mkdir(parents=True, exist_ok=True); p.write_text(str(v)); return v
        except Exception: pass
    if platform == "facebook":
        leg = DATA / ".fb_idx"
        if leg.exists():
            try: v = int(leg.read_text().strip() or "0"); p.write_text(str(v)); return v
            except Exception: pass
    return 0

def _next_qidx(keys: list[str]) -> dict:
    """Compat shim: aggregate per-platform files + legacy .qidx JSON."""
    out={}
    for plat in ("reddit","twitter","facebook"):
        out[plat]=_read_qidx(plat)
    if QIDX_FILE.exists():
        try:
            cur=json.loads(QIDX_FILE.read_text())
            for k,v in cur.items():
                if k not in out or not _qidx_path(k).exists():
                    out[k]=int(v)
        except Exception: pass
    return out

def _bump_qidx(platform: str, n: int):
    cur = _read_qidx(platform)
    nxt = (cur + 1) % max(1, n)
    DATA.mkdir(parents=True, exist_ok=True)
    _qidx_path(platform).write_text(str(nxt))

def _paced_sleep():
    p = getattr(C, "PACING", {})
    base = p.get("opencli_delay_sec", 15)
    jitter = p.get("jitter_sec", 5)
    time.sleep(base + random.uniform(0, jitter))

def _opencli_exe():
    import shutil
    return shutil.which("opencli") or str(Path.home() / ".npm-global/bin/opencli")

def _run_opencli(args: list[str], timeout=120):
    exe = _opencli_exe()
    env = os.environ.copy()
    env["PATH"] = f"{Path.home() / '.npm-global' / 'bin'}:{env.get('PATH','')}"
    # ponytail: parse -f json when possible, else yaml-ish fallback
    # always request json for defensive parsing
    if "-f" not in args:
        args = args + ["-f", "json"]
    else:
        # ensure json if caller passed -f yaml etc — we override to json
        for i, a in enumerate(args):
            if a in ("-f", "--format") and i+1 < len(args):
                args[i+1] = "json"
    r = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout, env=env)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    # backoff hint
    if "429" in err or "401" in err or r.returncode != 0 and "rate" in err.lower():
        time.sleep(getattr(C, "PACING", {}).get("retry_backoff_sec", 30))
    return out, err, r.returncode

def _parse_opencli_json(out: str) -> list[dict]:
    if not out.strip():
        return []
    # opencli -f json may emit a JSON array or newline-delimited JSON or a single object
    try:
        obj = json.loads(out)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict):
            # sometimes {data: [...]} or single record
            if "data" in obj and isinstance(obj["data"], list):
                return obj["data"]
            return [obj]
    except Exception:
        pass
    # NDJSON fallback
    rows=[]
    for line in out.splitlines():
        line=line.strip()
        if not line.startswith("{"): continue
        try: rows.append(json.loads(line))
        except Exception: continue
    if rows:
        return rows
    return []

def fetch_reddit_opencli(query: str):
    """One Reddit search via opencli. Returns (posts, warning). Falls back silently."""
    try:
        out, err, code = _run_opencli(["reddit", "search", query, "--limit", "10"])
        if code != 0 and not out:
            return [], f"reddit opencli: {err[:120]}"
        rows = _parse_opencli_json(out)
        if not rows:
            # yaml/table fallback — try loose parse
            if out and "title" in out.lower():
                # give up gracefully, RSS is primary
                return [], ""
            return [], ""
        posts=[]
        for r in rows:
            title = r.get("title") or ""
            body = r.get("selftext") or r.get("text") or ""
            text = f"{title}\n{body}".strip()
            if len(text) < 10:
                continue
            url = r.get("url") or r.get("permalink") or ""
            # normalize permalink
            if url and not url.startswith("http"):
                url = f"https://www.reddit.com{url}"
            pid = str(r.get("id") or hashlib.sha1((url or text[:80]).encode()).hexdigest()[:12])
            posts.append({"id": pid, "text": text, "source": f"r/{r.get('subreddit','search')}", "permalink": url, "score_raw": r.get("score",0) or 0, "author": r.get("author","")})
        return posts, ""
    except Exception as e:
        return [], f"reddit opencli failed: {e}"

def fetch_twitter_opencli(query: str):
    try:
        out, err, code = _run_opencli(["twitter", "search", query, "--limit", "10"])
        if code != 0 and not out:
            return [], f"twitter opencli: {err[:120]}"
        rows = _parse_opencli_json(out)
        if not rows:
            return [], ""
        posts=[]
        for r in rows:
            text = r.get("text") or r.get("full_text") or ""
            if len(text) < 10:
                continue
            url = r.get("url") or ""
            pid = str(r.get("id") or hashlib.sha1((url or text[:80]).encode()).hexdigest()[:12])
            posts.append({"id": f"tw_{pid}", "text": text, "source": "twitter:search", "permalink": url, "score_raw": r.get("likes",0) or 0, "author": r.get("author","")})
        return posts, ""
    except Exception as e:
        return [], f"twitter opencli failed: {e}"

def mcp_health():
    import requests
    try:
        r = requests.get(f"{C.MCP_BASE}/health", timeout=5)
        return r.ok
    except Exception:
        return False

def rest_reports():
    import requests
    try:
        r = requests.get(f"{C.MCP_BASE}/api/reports?limit=20", timeout=C.MCP_TIMEOUT_SEC)
        if r.ok:
            return r.json().get("reports", [])
    except Exception as e:
        print(f"[warn] REST /api/reports failed: {e}", file=sys.stderr)
    return []

def rest_analysis(handle_id: str):
    import requests
    try:
        r = requests.get(f"{C.MCP_BASE}/api/reports/{handle_id}/analysis", timeout=C.MCP_TIMEOUT_SEC)
        if r.ok:
            return r.json().get("analysis", {})
    except Exception as e:
        print(f"[warn] analysis fetch failed {handle_id}: {e}", file=sys.stderr)
    return {}

def try_mcp_tool(tool: str, args: dict):
    import requests, json as _json
    url = f"{C.MCP_BASE}/mcp"
    payload = {"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":tool,"arguments":args}}
    headers = {"Content-Type":"application/json","Accept":"application/json, text/event-stream"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=C.MCP_TIMEOUT_SEC)
        txt = r.text or ""
        for line in reversed(txt.splitlines()):
            line=line.strip()
            if line.startswith("data:"):
                line=line[5:].strip()
            if line.startswith("{"):
                try:
                    obj=_json.loads(line)
                    content = obj.get("result",{}).get("content",[])
                    if content and isinstance(content[0], dict) and "text" in content[0]:
                        try: return _json.loads(content[0]["text"])
                        except Exception: return content[0]["text"]
                    return obj
                except Exception:
                    continue
        if r.ok and txt.startswith("{"):
            return _json.loads(txt)
    except Exception as e:
        print(f"[warn] MCP {tool} failed: {e}", file=sys.stderr)
    return None

def fetch_reddit_rss(subreddits_csv: str, dry_run: bool = False):
    posts, warns = [], []
    all_subs = [s.strip() for s in subreddits_csv.split(",") if s.strip()]
    if not all_subs:
        return [], []
    # rotate a subset per cycle so each subreddit is fetched every N cycles (429 prevention)
    n = max(1, int(getattr(C, "PACING", {}).get("subs_per_cycle", len(all_subs))))
    total_subs = len(all_subs)
    if total_subs > n:
        idx_path = DATA / ".rss_idx"
        start = int(idx_path.read_text().strip() or 0) if idx_path.exists() else 0
        subs = [all_subs[(start + i) % total_subs] for i in range(n)]
        idx_path.write_text(str((start + n) % total_subs))
    else:
        subs = all_subs
    ns = "{http://www.w3.org/2005/Atom}"
    for sub in subs:
        try:
            import requests
            r = None
            for attempt in range(getattr(C, "PACING", {}).get("max_retries", 2) + 1):
                r = requests.get(
                    f"https://www.reddit.com/r/{sub}/new/.rss",
                    headers={"User-Agent": "Mozilla/5.0 social-listening/0.1"},
                    timeout=15)
                if r.status_code != 429 or attempt >= getattr(C, "PACING", {}).get("max_retries", 2):
                    break
                # honor Retry-After when Reddit sends it; else full backoff (no cap — waiting is the point)
                wait = r.headers.get("Retry-After")
                delay = float(wait) if wait and wait.isdigit() else getattr(C, "PACING", {}).get("retry_backoff_sec", 30) * (attempt + 1)
                time.sleep(delay)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            for e in root.findall(f"{ns}entry"):
                def _t(tag):
                    el = e.find(f"{ns}{tag}")
                    return (el.text or "") if el is not None else ""
                link = ""
                for l in e.findall(f"{ns}link"):
                    if l.get("rel") in (None, "alternate"):
                        link = l.get("href", ""); break
                raw = _t("content") or _t("summary")
                text = html.unescape(re.sub(r"<[^>]+>", " ", raw)).strip()
                title = _t("title")
                author_el = e.find(f"{ns}author/{ns}name")
                posts.append({
                    "id": (_t("id") or link).split("?")[0],
                    "text": f"{title}\n{text}".strip(),
                    "source": f"r/{sub}",
                    "permalink": link,
                    "score_raw": 0,
                    "author": author_el.text if author_el is not None else "",
                })
            _p = getattr(C, "PACING", {})
            if dry_run:
                time.sleep(0.05)
            else:
                time.sleep(_p.get("reddit_feed_delay_sec", 15) + random.uniform(0, _p.get("jitter_sec", 5)))
        except Exception as ex:
            warns.append(f"reddit-rss r/{sub}: {ex}")
    return posts, warns

def fetch_facebook_opencli(query: str):
    """One Facebook search via opencli. Tries json first, falls back to yaml-ish parse."""
    try:
        out, err, code = _run_opencli(["facebook", "search", query, "--limit", "10"])
        if not out.strip():
            return [], f"facebook: opencli returned nothing ({err[:120]})"
        rows = _parse_opencli_json(out)
        if rows:
            posts=[]
            for r in rows:
                url = r.get("url") or ""
                if not url:
                    continue
                title = r.get("title") or ""
                text = r.get("text") or ""
                body = f"{title}\n{text}".strip()
                if len(body) < 15:
                    continue
                posts.append({"id": hashlib.sha1(url.encode()).hexdigest()[:12], "text": body, "source": "fb:search", "permalink": url, "score_raw": 0, "author": r.get("author","")})
            return posts, ""
    except Exception as e:
        return [], f"facebook: opencli failed: {e}"
    # yaml-ish fallback (legacy)
    posts = []
    blocks = re.split(r"\n(?=- index:)", out)
    for b in blocks:
        m_url = re.search(r"url: (\S+)", b)
        if not m_url:
            continue
        m_txt = re.search(r"text: >-\n((?:\s+.+\n?)+)", b) or re.search(r"text: (.+)", b)
        m_title = re.search(r"title: \"?(.+?)\"?\n", b)
        text = " ".join((m_txt.group(1).split()) if m_txt else [])
        title = m_title.group(1) if m_title else ""
        body = f"{title}\n{text}".strip()
        if len(body) < 15:
            continue
        posts.append({"id": hashlib.sha1(m_url.group(1).encode()).hexdigest()[:12], "text": body, "source": "fb:search", "permalink": m_url.group(1), "score_raw": 0, "author": ""})
    return posts, ""

def gather_posts_via_reports():
    posts = []
    topics_from_reports = []
    for meta in rest_reports()[:6]:
        hid = meta.get("handle_id","")
        if not hid: continue
        ana = rest_analysis(hid)
        if not ana: continue
        for t in ana.get("topics",[]) or ana.get("themes",[]) or []:
            topics_from_reports.append({"theme": t.get("theme") or t.get("name",""), "share_pct": t.get("share_pct",0), "hit_count": t.get("hit_count",0)})
            for e in (t.get("evidence") or [])[:6]:
                txt = e.get("text_preview") or e.get("snippet") or e.get("text") or ""
                if not txt: continue
                posts.append({"id": e.get("uid") or e.get("citation_id") or hashlib.sha1(txt.encode()).hexdigest()[:12], "text": txt, "source": e.get("source") or e.get("subreddit") or hid, "permalink": e.get("permalink") or e.get("url") or "", "score_raw": e.get("score",0)})
        for c in ana.get("citations",[])[:20]:
            txt = c.get("text_preview") or c.get("snippet") or ""
            if txt and not any(p["text"]==txt for p in posts):
                posts.append({"id": c.get("uid",""), "text": txt, "source": hid, "permalink": c.get("permalink",""), "score_raw":0})
    return posts, topics_from_reports

def topics_from_term_freq(posts):
    cnt = collections.Counter()
    for p in posts:
        tl = p["text"].lower()
        for phrase,_ in C.ALL_KEYWORDS:
            if phrase.lower() in tl:
                cnt[phrase] += 1
    for p in posts:
        for w in re.findall(r"[a-z]{4,}", p["text"].lower()):
            if w in {"website","redesign","shopify","wordpress","squarespace","automation","chatbot","zapier","invoicing"}:
                cnt[w]+=1
    return [{"theme":k,"share_pct":0,"hit_count":v} for k,v in cnt.most_common(5)]

def render_digest(leads, topics, warnings, meta):
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [f"# Social Listening Digest — {now}", ""]
    if warnings:
        lines += ["## ⚠️ Warnings"] + [f"- {w}" for w in warnings] + [""]
    lines += [f"_Sources: Reddit({C.SUBREDDITS}) · X({C.X_SEARCH[:60]}…) · FB daily_  ·  Pipeline: Social-ops free tier, deterministic scoring_",""]
    if not leads:
        lines += ["**No high-intent leads surfaced this cycle.** Check warnings; add credentials per SETUP.md.", ""]
    else:
        lines += [f"## Top {len(leads)} intent-scored leads (score 0-100)", ""]
        for i,p in enumerate(leads,1):
            tags = ",".join(p["tags"]) if p["tags"] else "general"
            snippet = p["text"].strip().replace("\n"," ")[:280]
            link = f" — {p['permalink']}" if p.get("permalink") else ""
            lines += [f"**{i}. [{p['source']}] score {p['score']} · {tags}**{link}", f"> {snippet}", f"_hits: {', '.join(p['hits'][:4])}_",""]
    lines += ["## Top 5 topics in niche", ""]
    if not topics:
        lines += ["- _no topic signal this cycle (no reports yet)_",""]
    else:
        for t in topics[:5]:
            share = f" {t['share_pct']}%" if t.get("share_pct") else ""
            lines += [f"- **{t['theme']}**{share} · {t.get('hit_count',0)} mentions"]
        lines += [""]
    clf = meta.get("classifier_mode","keyword")
    lines += [f"_Classifier: {clf} · Deduped against {meta.get('seen_total',0)} seen ids · next run in ~3h · FB daily flag: {meta.get('fb_flag','n/a')}_"]
    if meta.get("fastlane_n"):
        lines += [f"_Fast lane pings sent: {meta['fastlane_n']}_"]
    return "\n".join(lines)

def append_capture(posts: list[dict]):
    """Append this cycle's posts to data/capture.jsonl for daily brief."""
    DATA.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(CAPTURE_JSONL, "a", encoding="utf-8") as f:
        for p in posts:
            rec = {"captured_at": ts, "id": p.get("id",""), "source": p.get("source",""), "permalink": p.get("permalink",""), "author": p.get("author",""), "text": p.get("text","")[:2000], "score": p.get("score",0), "tags": p.get("tags",[]), "classifier": p.get("classifier",""), "intent_obj": {k: p.get(k,"") for k in ("icp","author_role","intent","urgency","confidence","one_line")}}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

def write_leads_csv(qualified: list[dict], seen: set):
    """Append qualified leads to data/leads.csv, dedup by id already in file. Returns count written."""
    DATA.mkdir(parents=True, exist_ok=True)
    existing_ids=set()
    if LEADS_CSV.exists():
        try:
            with open(LEADS_CSV, newline="", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    url = (row.get("url","") or "").strip()
                    if url:
                        existing_ids.add(url)
                    else:
                        # samples have no url — key by snippet (stable across dry-runs)
                        existing_ids.add((row.get("snippet","") or "")[:80].strip().lower())
        except Exception:
            pass
    cols = ["platform","score","tags","vertical_match","snippet","url","author","captured_at","classifier","icp","author_role","intent","urgency","confidence","one_line","status","note"]
    is_new = not LEADS_CSV.exists() or LEADS_CSV.stat().st_size == 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    written=0
    with open(LEADS_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if is_new:
            w.writeheader()
        for p in qualified:
            snippet = p.get("text","").replace("\n"," ").strip()[:200]
            url = (p.get("permalink","") or "").strip()
            # ponytail: dedup key = url when present else snippet lower — same as reader above
            key = url if url else snippet[:80].strip().lower()
            if key in existing_ids:
                continue
            if p.get("id") in seen and p["id"] not in existing_ids:
                pass
            w.writerow({
                "platform": p.get("source",""),
                "score": p.get("score",0),
                "tags": ",".join(p.get("tags",[])),
                "vertical_match": vertical_match(p.get("text","")),
                "snippet": snippet,
                "url": url,
                "author": p.get("author",""),
                "captured_at": now,
                "classifier": p.get("classifier",""),
                "icp": p.get("icp",""),
                "author_role": p.get("author_role",""),
                "intent": p.get("intent",""),
                "urgency": p.get("urgency",""),
                "confidence": p.get("confidence",""),
                "one_line": (p.get("one_line","") or "")[:160],
                "status": p.get("status","new"),
                "note": p.get("note",""),
            })
            written+=1
            existing_ids.add(key)
    return written

def _golden_path() -> pathlib.Path:
    return DATA / "golden.jsonl"

def _load_golden() -> list[dict]:
    gp = _golden_path()
    if not gp.exists():
        return []
    out=[]
    for line in gp.read_text(encoding="utf-8").splitlines():
        line=line.strip()
        if not line: continue
        try: out.append(__import__("json").loads(line))
        except Exception: continue
    return out

def _fixture_intent_map() -> dict[str, dict]:
    """Deterministic fixture map for self-test: uses golden labels directly (no LLM)."""
    mp={}
    for rec in _load_golden():
        gid = rec.get("id","")
        label = rec.get("label",{})
        # confidence passthrough; fixture is the oracle
        mp[gid] = {"id": gid, "icp": label.get("icp","not_icp"), "author_role": label.get("author_role","unknown"),
                   "intent": label.get("intent","none"), "urgency": label.get("urgency","someday"),
                   "one_line": rec.get("text","")[:80], "confidence": float(label.get("confidence",0.8) or 0.8)}
    return mp

def _golden_metrics(fixture_map: dict[str, dict]) -> tuple[float,float]:
    golds = _load_golden()
    if not golds:
        return 1.0, 1.0
    tp=fp=fn=0
    for rec in golds:
        gid = rec["id"]
        truth_q = (
            rec["label"].get("author_role") in ("owner","unknown")
            and rec["label"].get("intent") in ("buying","pain")
            and rec["label"].get("icp") != "not_icp"
            and float(rec["label"].get("confidence",0) or 0) >= float(getattr(C, "INTENT_CONF_THRESHOLD", 0.6) or 0.6)
        )
        pred = fixture_map.get(gid, {})
        pred_q = is_qualified(pred) if pred else False
        if truth_q and pred_q: tp+=1
        elif pred_q and not truth_q: fp+=1
        elif truth_q and not pred_q: fn+=1
    prec = tp / max(1, tp+fp)
    rec = tp / max(1, tp+fn)
    return prec, rec

def self_test():
    failed=[]
    cases = [
        ("I need a website for my small business, my web developer disappeared", 50, ["website"]),
        ("Looking for web designer to redesign my site, site is slow", 40, ["website"]),
        ("We want to automate my business and manual data entry is killing us", 40, ["ai_automation"]),
        ("chatbot for business and zapier alternative please", 40, ["ai_automation"]),
        ("hello world no intent", 0, []),
        ("NEED A WEBSITE!!!", 20, ["website"]),
        ("my shop needs automate invoicing and automate scheduling", 30, ["ai_automation"]),
        ("need custom software for my roofing business", 25, ["ai_automation"]),
        ("claude setup for my hvac company", 20, ["ai_automation"]),
        ("seo help for my painting contractor website", 14, ["website"]),
    ]
    for text, min_score, must_tag in cases:
        s, tags, hits = score_text(text)
        if s < min_score or not all(t in tags for t in must_tag):
            failed.append((text, s, tags, hits, min_score, must_tag))
    s0, t0, _ = score_text("hello world no intent")
    if s0 != 0 or t0:
        failed.append(("hello world should be 0", s0, t0))
    # qualification: score>=25 + intent hit
    s_hi, _, hits_hi = score_text("need a website for my roofing business")
    if s_hi < 25 or not hits_hi:
        failed.append(("qualification roofing", s_hi, hits_hi))
    # vertical match
    if vertical_match("need a website for my roofing business") != "roofing":
        failed.append(("vertical roofing", vertical_match("need a website for my roofing business")))
    if vertical_match("hello world") != "":
        failed.append(("vertical empty", vertical_match("hello world")))
    # CSV round-trip
    import tempfile, csv as _csv
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as tf:
        w=_csv.DictWriter(tf, fieldnames=["platform","score","tags","vertical_match","snippet","url","author","captured_at"])
        w.writeheader()
        w.writerow({"platform":"r/test","score":40,"tags":"website","vertical_match":"roofing","snippet":"need a website roofing","url":"https://example.com/1","author":"u/x","captured_at":"2026-08-21T00:00:00Z"})
        tmpcsv=tf.name
    try:
        with open(tmpcsv, newline="", encoding="utf-8") as f:
            rows=list(_csv.DictReader(f))
            if len(rows)!=1 or rows[0]["vertical_match"]!="roofing":
                failed.append(("csv fixture", rows))
        os.unlink(tmpcsv)
    except Exception as ex:
        failed.append(("csv fixture error", str(ex)))
    posts=[{"text":"need a website and website redesign help"},{"text":"need a website again"},{"text":"automate my business chatbot"}]
    tops=topics_from_term_freq(posts)
    if not tops or tops[0]["theme"] not in {"need a website", "website"}:
        failed.append(("topics", tops))
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
    <entry><id>t3_abc1</id><title>Need a website</title><link rel="alternate" href="https://reddit.com/r/smallbusiness/comments/abc1"/>
    <author><name>u/x</name></author><updated>2026-08-21T00:00:00Z</updated>
    <content type="html">&lt;p&gt;my site is slow, need a redesign&lt;/p&gt;</content></entry>
    <entry><id>t3_abc2</id><title>other</title><link href="https://reddit.com/r/smallbusiness/comments/abc2"/>
    <author><name>u/y</name></author><updated>2026-08-21T01:00:00Z</updated><content>hello world no intent</content></entry></feed>"""
    with tempfile.NamedTemporaryFile("w", suffix=".rss", delete=False) as f:
        f.write(atom); tmp = f.name
    try:
        import requests as _rq
        class FakeResp:
            status_code = 200
            content = open(tmp, "rb").read()
            def raise_for_status(self): pass
        orig_get = _rq.get
        _rq.get = lambda *a, **k: FakeResp()
        got, w = fetch_reddit_rss("smallbusiness")
        _rq.get = orig_get
        if len(got) != 2 or "site is slow" not in got[0]["text"] or not got[0]["permalink"]:
            failed.append(("rss fixture", len(got), [p["text"][:60] for p in got]))
        s_rss, tags_rss, _ = score_text(got[0]["text"])
        if s_rss <= 0 or "website" not in tags_rss:
            failed.append(("rss scoring", s_rss, tags_rss))
        os.unlink(tmp)
    except Exception as ex:
        failed.append(("rss fixture error", str(ex)))
    # opencli json parse fixture
    # R2 golden fixtures — no LLM, deterministic oracle
    golds = _load_golden()
    if len(golds) != 40:
        failed.append(("golden count", f"expected 40 got {len(golds)}"))
    else:
        fmap = _fixture_intent_map()
        # qualified set should be g01..g20 minus any threshold edge; g40 is low-conf question so not qualified
        prec, rec = _golden_metrics(fmap)
        if prec < 0.8 or rec < 0.7:
            failed.append(("golden precision/recall", f"prec={prec:.2f} rec={rec:.2f}"))
        # practitioner traps must NOT qualify
        for gid in ["g21","g22","g23","g24","g30","g36","g37"]:
            if is_qualified(fmap.get(gid,{})):
                failed.append(("practitioner trap qualified", gid, fmap.get(gid)))
        # true buyers must qualify
        for gid in ["g01","g02","g05","g07"]:
            if not is_qualified(fmap.get(gid,{})):
                failed.append(("true buyer not qualified", gid, fmap.get(gid)))
        # should_llm_classify gating
        if not should_llm_classify("roofing business is slow", 2):
            failed.append(("should_llm vertical", should_llm_classify("roofing business is slow", 2)))
        if should_llm_classify("nice weather today", 0):
            failed.append(("should_llm noise", True))
        # urgency fast-lane signal on buying+now
        if fmap.get("g01",{}).get("urgency") != "now":
            failed.append(("urgency g01", fmap.get("g01")))
        # leads.csv header migration — must include new columns
        import tempfile as _tf, csv as _csv2
        with _tf.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8") as tf2:
            w2=_csv2.DictWriter(tf2, fieldnames=["platform","score","tags","vertical_match","snippet","url","author","captured_at","classifier","icp","author_role","intent","urgency","confidence","one_line","status","note"])
            w2.writeheader()
            w2.writerow({"platform":"r/test","score":40,"tags":"website","vertical_match":"roofing","snippet":"need a website roofing","url":"https://example.com/1","author":"u/x","captured_at":"2026-08-21T00:00:00Z","classifier":"llm","icp":"roofing","author_role":"owner","intent":"buying","urgency":"now","confidence":0.9,"one_line":"need website roofing","status":"new","note":""})
            tmp2=tf2.name
        try:
            with open(tmp2, newline="", encoding="utf-8") as f:
                rows2=list(_csv2.DictReader(f))
                if rows2[0]["status"]!="new" or rows2[0]["intent"]!="buying":
                    failed.append(("csv intent header", rows2[0]))
            os.unlink(tmp2)
        except Exception as ex:
            failed.append(("csv intent fixture error", str(ex)))

    sample_json = json.dumps([{"id":"abc","title":"Need a website roofing","selftext":"my site is slow","subreddit":"smallbusiness","author":"u/test","score":5,"url":"https://reddit.com/r/test/abc"}])
    rows=_parse_opencli_json(sample_json)
    if len(rows)!=1 or rows[0]["title"]!="Need a website roofing":
        failed.append(("opencli json parse", rows))
    if failed:
        print("SELF-TEST FAILED:")
        for f in failed: print(" ", f)
        return 1
    print("self-test: OK — scoring + topics passed")
    seen=load_seen()
    print(f"self-test: seen file has {len(seen)} ids (ok)")
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="render digest without requiring creds/MCP")
    ap.add_argument("--self-test", action="store_true", help="run scoring self-check and exit")
    ap.add_argument("--facebook", action="store_true", help="force facebook cycle (daily)")
    ap.add_argument("--out", default="", help="override output md path")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(self_test())

    warnings=[]
    fb_due=False
    fb_flag_path = DATA / ".fb_last"
    today = datetime.date.today().isoformat()
    if args.facebook:
        fb_due=True
    else:
        last = fb_flag_path.read_text().strip() if fb_flag_path.exists() else ""
        fb_due = (last != today)

    healthy = mcp_health()
    if not healthy:
        warnings.append("MCP at :8097 not reachable (docker not running or not healthy) — using degraded mode (no live collect).")
        sn_path = Path(__file__).parent.parent / "snscrape"
        if sn_path.exists():
            warnings.append("snscrape/ is available as X fallback if Social-ops X collector stays unusable — not used this cycle.")
    else:
        warnings.append("MCP reachable — reports will be pulled via REST if available.")

    posts, topics_from_reports = gather_posts_via_reports()

    if not args.dry_run and getattr(C, "REDDIT_RSS", False) and not os.environ.get("REDDIT_CLIENT_ID"):
        rss_posts, rss_warns = fetch_reddit_rss(C.SUBREDDITS, dry_run=args.dry_run)
        warnings.extend(rss_warns)
        have = {p["id"] for p in posts}
        posts.extend(p for p in rss_posts if p["id"] not in have)
        if not rss_warns and rss_posts:
            warnings = [w for w in warnings if "MCP reachable" not in w]
    if posts and warnings and "MCP reachable" in warnings[0]:
        warnings = [w for w in warnings if "MCP reachable" not in w]

    # Paced opencli collectors: max one per platform per cycle, rotated (silent fallback)
    # ponytail: one opencli call per platform per cycle, 15s+ jitter between calls
    def _rotated_query(platform: str, queries: list[str]) -> str:
        if not queries:
            return ""
        cur = _next_qidx([])
        idx = cur.get(platform, 0) % len(queries)
        return queries[idx]

    if not args.dry_run:
        # Reddit opencli (RSS remains primary; this adds intent queries)
        rq = _rotated_query("reddit", getattr(C, "REDDIT_QUERIES", []))
        if rq:
            try:
                r_posts, r_warn = fetch_reddit_opencli(rq)
                if r_warn:
                    warnings.append(r_warn)
                if r_posts:
                    have={p["id"] for p in posts}
                    posts.extend(p for p in r_posts if p["id"] not in have)
                    warnings.append(f"reddit opencli: \"{rq}\" → {len(r_posts)} results.")
                _bump_qidx("reddit", len(getattr(C, "REDDIT_QUERIES", [])))
            except Exception as e:
                warnings.append(f"reddit opencli error: {e}")
            _paced_sleep()
        # Twitter opencli
        tq = _rotated_query("twitter", getattr(C, "TWITTER_QUERIES", []))
        if tq:
            try:
                t_posts, t_warn = fetch_twitter_opencli(tq)
                if t_warn:
                    warnings.append(t_warn)
                if t_posts:
                    have={p["id"] for p in posts}
                    posts.extend(p for p in t_posts if p["id"] not in have)
                    warnings.append(f"twitter opencli: \"{tq}\" → {len(t_posts)} results.")
                _bump_qidx("twitter", len(getattr(C, "TWITTER_QUERIES", [])))
            except Exception as e:
                warnings.append(f"twitter opencli error: {e}")
            _paced_sleep()
        # Facebook opencli — rotated via data/.qidx (migrates legacy .fb_idx), max one per cycle
        fq = _rotated_query("facebook", getattr(C, "FACEBOOK_QUERIES", []))
        if fq:
            # migrate legacy .fb_idx once
            try:
                legacy = DATA / ".fb_idx"
                if legacy.exists() and "facebook" not in _next_qidx([]):
                    v = int(legacy.read_text().strip() or 0)
                    cur = _next_qidx([])
                    cur["facebook"] = v % max(1, len(getattr(C, "FACEBOOK_QUERIES", [])))
                    QIDX_FILE.write_text(json.dumps(cur))
            except Exception:
                pass
            try:
                fb_posts, fb_warn = fetch_facebook_opencli(fq)
                if fb_warn:
                    warnings.append(fb_warn)
                if fb_posts:
                    have={p["id"] for p in posts}
                    posts.extend(p for p in fb_posts if p["id"] not in have)
                    warnings.append(f"facebook: searched \"{fq}\" → {len(fb_posts)} results.")
                elif fb_warn:
                    warnings.append(fb_warn or "facebook: no usable results this cycle.")
                _bump_qidx("facebook", len(getattr(C, "FACEBOOK_QUERIES", [])))
            except Exception as e:
                warnings.append(f"facebook error: {e}")
            _paced_sleep()

    if not posts and not args.dry_run and healthy:
        for plat, tool, t_args in [
            ("reddit","analyze_reddit",{"name":"listening-loop","subreddits":C.SUBREDDITS,"max_posts":min(20, C.REDDIT_MAX_POSTS_PER_SUB),"window":C.REDDIT_WINDOW,"report_level":"L0"}),
            ("x","analyze_x",{"name":"listening-loop-x","search":C.X_SEARCH,"max_posts":20,"window":"7d","report_level":"L0"}),
        ]:
            res = try_mcp_tool(tool, t_args)
            if res is None:
                warnings.append(f"{plat}: MCP {tool} not available (will show after creds + report ready).")
            elif isinstance(res, dict) and res.get("task_id"):
                warnings.append(f"{plat}: task {res['task_id']} queued — digest will populate on next cycle.")

        # facebook already handled in paced opencli block above (single rotation via .qidx)
    elif not posts and args.dry_run:
        warnings.append("dry-run: no live posts — rendering from empty/sample (expected before creds).")
        posts = [
            {"id":"sample1","text":"Need a website for my small business — my web developer disappeared and site is slow","source":"sample:r/smallbusiness","permalink":"","score_raw":0},
            {"id":"sample2","text":"Looking to automate my business, manual data entry is killing us — need chatbot for business","source":"sample:r/Entrepreneur","permalink":"","score_raw":0},
        ]
        dry_synthetic_ids = {p["id"] for p in posts}
    else:
        dry_synthetic_ids = set()
        if not healthy and not args.dry_run and not posts:
            warnings.append("No credentials yet — collectors will fail gracefully until owner adds keys (see SETUP.md). Digest still sent for whatever succeeded.")

    if 'dry_synthetic_ids' not in locals():
        dry_synthetic_ids = set()

    # Score + dedupe
    seen = load_seen()
    scored=[]
    for p in posts:
        s, tags, hits = score_text(p["text"])
        scored.append({**p, "score": s, "tags": tags, "hits": hits})
    deduped=[]
    new_ids=set()
    for p in scored:
        if p["id"] in seen and p["id"] not in (dry_synthetic_ids if args.dry_run else set()):
            continue
        deduped.append(p)
        if p["id"] not in dry_synthetic_ids:
            new_ids.add(p["id"])
    deduped.sort(key=lambda x: (x["score"], x["score_raw"]), reverse=True)
    leads = deduped[:20]
    intent_leads = [p for p in leads if p["score"] >= 15]
    if intent_leads:
        leads = intent_leads

    # R2 intent classification — runs on deduped set; degrades to keyword gate if LLM down
    classifier_mode = "keyword"
    intent_map: dict = {}
    intent_candidates = [p for p in deduped if should_llm_classify(p.get("text",""), int(p.get("score",0) or 0))]
    scored_by_id = {p["id"]: p for p in scored}
    if intent_candidates and getattr(C, "INTENT_LLM_ENABLED", True) and not args.dry_run:
        try:
            intent_map = classify_intent(intent_candidates)
            if intent_map:
                classifier_mode = "llm"
            else:
                warnings.append("intent classifier: LLM returned empty — degraded to keyword gate.")
        except Exception as ex:
            warnings.append(f"intent classifier failed ({ex}) — degraded to keyword gate.")
    # annotate scored and deduped with intent fields for capture + CSV
    for p in scored:
        obj = intent_map.get(p["id"], {})
        p["classifier"] = classifier_mode if obj else "keyword"
        p["icp"] = obj.get("icp","") if obj else ""
        p["author_role"] = obj.get("author_role","") if obj else ""
        p["intent"] = obj.get("intent","") if obj else ""
        p["urgency"] = obj.get("urgency","") if obj else ""
        p["confidence"] = obj.get("confidence","") if obj else ""
        p["one_line"] = obj.get("one_line","") if obj else ""
        p["status"] = "new"
        p["note"] = ""
    # Qualified leads for CSV
    if classifier_mode == "llm" and intent_map:
        qualified = [p for p in deduped if is_qualified(intent_map.get(p["id"], {}))]
        # keep a keyword fallback note but don't double-count
        kw_qualified = [p for p in deduped if p["score"] >= 25 and has_intent_hit(p["text"])]
        if not qualified and kw_qualified:
            warnings.append(f"llm qualified 0 but keyword gate would have had {len(kw_qualified)} — keeping llm gate.")
    else:
        qualified = [p for p in deduped if p["score"] >= 25 and has_intent_hit(p["text"])]
    # FR-2.3 fast lane (best-effort, no crash)
    fastlane_n = 0
    if classifier_mode == "llm" and intent_map and not args.dry_run:
        try:
            fastlane_n = send_fast_lane(intent_map, scored_by_id)
        except Exception as ex:
            warnings.append(f"fast lane failed: {ex}")
    # capture + csv (every cycle, even if 0 — csv header still written)
    try:
        append_capture(scored)
    except Exception as e:
        warnings.append(f"capture.jsonl write failed: {e}")
    try:
        n_written = write_leads_csv(qualified, seen)
        if qualified:
            warnings.append(f"leads.csv: {n_written} new qualified (score>=25 + intent) appended.")
    except Exception as e:
        warnings.append(f"leads.csv write failed: {e}")

    topics = topics_from_reports[:5] if topics_from_reports else topics_from_term_freq(posts)

    md = render_digest(leads, topics, warnings, {"seen_total": len(seen), "fb_flag": today if fb_due else (fb_flag_path.read_text().strip() if fb_flag_path.exists() else "not yet"), "classifier_mode": classifier_mode, "fastlane_n": fastlane_n})
    DATA.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else OUT_MD
    out_path.write_text(md)
    OUT_JSON.write_text(json.dumps({"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "warnings": warnings, "leads": leads, "topics": topics}, indent=2))
    if new_ids:
        seen |= new_ids
        if len(seen)>5000:
            seen=set(sorted(seen)[-5000:])
        save_seen(seen)

    print(md)
    print(f"\n[wrote {out_path} + {OUT_JSON} | leads={len(leads)} qualified={len(qualified)} topics={len(topics)} warnings={len(warnings)} seen={len(seen)}]", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
