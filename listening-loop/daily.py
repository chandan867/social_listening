#!/usr/bin/env python3
"""Daily trend brief: top topics + per-platform + YouTube transcripts. No LLM."""
from __future__ import annotations
import argparse, collections, datetime, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
CAPTURE_JSONL = DATA / "capture.jsonl"
OUT_MD = DATA / "daily.md"
OUT_JSON = DATA / "daily.json"

sys.path.insert(0, str(HERE))
import config as C  # noqa: E402

YOUTUBE_KEYWORDS = [p for p,_ in C.ALL_KEYWORDS] + ["ai","seo","llm","agent"]

def load_captures(day: str | None = None) -> list[dict]:
    if not CAPTURE_JSONL.exists():
        return []
    out=[]
    for line in CAPTURE_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        try:
            rec=json.loads(line)
        except Exception:
            continue
        if day and not rec.get("captured_at","").startswith(day):
            continue
        out.append(rec)
    return out

def top_topics(posts: list[dict], k=10) -> list[dict]:
    cnt = collections.Counter()
    examples: dict[str, list[str]] = collections.defaultdict(list)
    for p in posts:
        tl = p.get("text","").lower()
        for phrase,_ in C.ALL_KEYWORDS:
            if phrase.lower() in tl:
                cnt[phrase]+=1
                if p.get("permalink") and p["permalink"] not in examples[phrase]:
                    examples[phrase].append(p["permalink"])
    # also count vertical terms
    for p in posts:
        tl = p.get("text","").lower()
        for v in getattr(C, "VERTICAL_TERMS", []):
            if v.lower() in tl:
                cnt[v]+=1
                if p.get("permalink") and p["permalink"] not in examples[v]:
                    examples[v].append(p["permalink"])
    top=[]
    for theme, n in cnt.most_common(k):
        top.append({"topic": theme, "count": n, "examples": examples[theme][:3]})
    return top

def per_platform_blocks(posts: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {"Reddit":[], "Twitter":[], "Facebook":[], "YouTube":[]}
    for p in posts:
        s = (p.get("source") or "").lower()
        if s.startswith("r/") or "reddit" in s:
            buckets["Reddit"].append(p)
        elif "twitter" in s or s.startswith("tw_"):
            buckets["Twitter"].append(p)
        elif "fb" in s or "facebook" in s:
            buckets["Facebook"].append(p)
        else:
            buckets["Reddit"].append(p)
    return buckets

def fetch_youtube_briefs(max_videos=5) -> list[dict]:
    """yt-dlp: pull subtitles for a few videos from configured channels. Deterministic only."""
    import shutil
    yt = shutil.which("yt-dlp")
    if not yt:
        candidates = [
            Path(__file__).resolve().parent.parent / ".venv" / "bin" / "yt-dlp",
            Path(__file__).resolve().parent.parent / ".venv-ar" / "bin" / "yt-dlp",
            Path.home() / ".local" / "bin" / "yt-dlp",
        ]
        for c in candidates:
            if c.exists():
                yt = str(c)
                break
    if not yt:
        yt = "yt-dlp"
    briefs=[]
    channels = getattr(C, "YOUTUBE_CHANNELS", [])[:5]
    # ponytail: one yt-dlp call per channel at most, skip failures gracefully
    for handle in channels:
        if len(briefs) >= max_videos:
            break
        # use channel handle as URL; yt-dlp handles @handle
        url = f"https://www.youtube.com/{handle}/videos"
        try:
            # list recent video ids (flat)
            r = subprocess.run([yt, "--flat-playlist", "--print", "%(id)s %(title)s", "--playlist-end", "2", url],
                               capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not r.stdout.strip():
                continue
            for line in r.stdout.splitlines()[:2]:
                if len(briefs) >= max_videos:
                    break
                vid = line.split()[0] if line.strip() else ""
                title = line[len(vid):].strip() if vid else line.strip()
                if not vid or len(vid) < 5:
                    continue
                # fetch auto subtitles (vtt) -> text
                vurl = f"https://www.youtube.com/watch?v={vid}"
                # try to get subtitles via --write-auto-sub --skip-download
                import tempfile, os, glob
                with tempfile.TemporaryDirectory() as td:
                    sr = subprocess.run([yt, "--write-auto-sub", "--sub-lang", "en", "--skip-download", "--output", f"{td}/%(id)s.%(ext)s", vurl],
                                        capture_output=True, text=True, timeout=90)
                    vtts = glob.glob(f"{td}/*.vtt")
                    lines=[]
                    for vp in vtts:
                        try:
                            txt = Path(vp).read_text(encoding="utf-8", errors="ignore")
                            # strip vtt headers/timestamps
                            for l in txt.splitlines():
                                l=l.strip()
                                if not l or l.startswith("WEBVTT") or "-->" in l or l.startswith("NOTE") or re.match(r"^\d+$", l):
                                    continue
                                # strip html tags
                                l=re.sub(r"<[^>]+>","",l).strip()
                                if l:
                                    lines.append(l)
                        except Exception:
                            continue
                    # dedupe consecutive dupes
                    ded=[]
                    for l in lines:
                        if not ded or ded[-1]!=l:
                            ded.append(l)
                    # top 5 lines containing domain keywords
                    hits=[]
                    for l in ded:
                        ll=l.lower()
                        if any(kw.lower() in ll for kw in YOUTUBE_KEYWORDS):
                            hits.append(l)
                        if len(hits)>=5:
                            break
                    if not hits:
                        hits = ded[:5]
                    briefs.append({"title": title or vid, "url": vurl, "lines": hits[:5]})
        except Exception:
            continue
    return briefs

def fetch_trend_tweets():
    """One rotated Twitter search on domain topics (not lead queries). Returns list of trend posts."""
    import digest as D
    queries = getattr(C, "TREND_TWITTER_QUERIES", [])
    if not queries:
        return []
    idx_path = DATA / ".trend_qidx"
    idx = int(idx_path.read_text().strip() or 0) if idx_path.exists() else 0
    idx_path.write_text(str(idx + 1))
    q = queries[idx % len(queries)]
    posts, _warn = D.fetch_twitter_opencli(q)
    for p in posts:
        p["source"] = f"twitter:trend ({q})"
    return posts

def llm_summary(posts: list[dict], topics: list[dict], trend_posts: list[dict] | None = None, yt_briefs: list[dict] | None = None) -> str:
    """LLMified narrative of the day's discussion via local free gateway. Returns "" on failure."""
    import requests
    try:
        # Trend feed (twitter topic searches + youtube transcripts) drives What's trending / Worth knowing;
        # lead posts drive content angles. Separate streams on purpose.
        trend_posts = trend_posts or []
        def _fmt(pl, n=40):
            return "\n".join(f"[{p.get('source','?')}] {(p.get('text','') or '').replace(chr(10),' ')[:260]}" for p in pl[:n])
        yt_text = "\n".join(f"[youtube] {b['title']}: " + " ".join(b["lines"][:3]) for b in (yt_briefs or []))
        trend_blob = _fmt(trend_posts) + ("\n" + yt_text if yt_text else "")
        prompt = (
            "You write social posts for a founder who is BUILDING an AI-native agency in public — learning, "
            "experimenting, sharing notes. The audience is US local businesses (roofing, solar, HVAC, painting) "
            "and recruitment agencies. The founder sells web design, SEO, custom software and AI automation.\n\n"
            "TREND FEED (tweets + YouTube transcript excerpts about AI/LLM/SEO topics):\n"
            f"{trend_blob[:12000]}\n\n"
            f"LEAD POSTS (real things business owners said today; use their exact pain language):\n"
            f"{_fmt(posts)[:8000]}\n\n"
            "VOICE RULES (critical):\n"
            "- First person, builder/learner energy: 'I came across...', 'I tested...', 'still figuring out...'. NEVER guru/preacher tone, no 'The solution is to...', no hustle-bro advice, no 'Send us a message' CTAs.\n"
            "- ALWAYS cite the source inline and conversationally: name the tweet/video/post you got it from ('saw @simonw note that...', 'in Matt Wolfe's latest video he mentions...'). If the feed has no attributable source for a claim, cut the claim.\n"
            "- It's fine to end with an open question or 'curious what others are seeing' — never a sales pitch.\n"
            "- Small imperfections welcome (asides, honest doubts). Vary sentence length. No more than one hashtag total across all posts.\n\n"
            "Write READY-TO-PUBLISH drafts derived ONLY from content above — every claim must trace to the feeds; never invent stats, names or quotes.\n"
            "Output markdown:\n"
            "## LinkedIn post\n(100-150 words, learning-in-public vibe: what I saw today -> why it matters to trade/recruiting businesses -> what I'm trying with it)\n"
            "## Reddit post/comment angle\n(casual peer-to-peer, 80-120 words, share the finding as something you stumbled on, invite pushback)\n"
            "## Facebook post\n(relaxed, 60-90 words, like telling a friend what you learned today)\n"
            "## Source notes\n(bullet per draft mapping each factual claim to its exact source in the feeds)\n"
        )
        r = requests.post(
            f"{C.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {C.LLM_API_KEY}"},
            json={"model": C.LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 700},
            timeout=90)
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as e:
        print(f"[daily] llm summary unavailable ({e}) — keeping deterministic brief", file=sys.stderr)
        return ""

def render_daily(day: str, posts: list[dict], topics: list[dict], per_platform: dict, yt_briefs: list[dict], trend_posts: list[dict] | None = None) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines=[f"# Daily Brief — {day}  _(generated {now})_", ""]
    lines+=[f"_Captured posts today: {len(posts)}_", ""]
    summary = llm_summary(posts, topics, trend_posts=trend_posts, yt_briefs=yt_briefs)
    if summary:
        lines+=[summary, ""]
        lines+=["---", ""]
    lines+=["## Top 10 most-discussed topics", ""]
    if not topics:
        lines+=["- _no topics today (no captures yet)_",""]
    else:
        for t in topics:
            ex = " · ".join(t["examples"][:3]) if t["examples"] else "_no links_"
            lines+=[f"- **{t['topic']}** — {t['count']} mentions — {ex}"]
        lines+=[""]
    for plat in ["Reddit","Twitter","Facebook","YouTube"]:
        lines+=[f"## {plat}", ""]
        if plat == "YouTube":
            if not yt_briefs:
                lines+=["- _no youtube transcripts today (channels unavailable or no captions)_",""]
            else:
                for b in yt_briefs:
                    lines+=[f"**{b['title']}** — {b['url']}"]
                    for l in b["lines"]:
                        lines+=[f"> {l[:220]}"]
                    lines+=[""]
        else:
            bucket = per_platform.get(plat, [])
            if not bucket:
                lines+=["- _no posts_",""]
            else:
                # show top 5 by score
                bucket_sorted = sorted(bucket, key=lambda x: x.get("score",0), reverse=True)[:5]
                for p in bucket_sorted:
                    snippet = p.get("text","").replace("\n"," ")[:180]
                    link = f" — {p['permalink']}" if p.get("permalink") else ""
                    lines+=[f"- **score {p.get('score',0)}** {snippet}{link}"]
                lines+=[""]
    return "\n".join(lines)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="render without network (youtube skipped)")
    ap.add_argument("--date", default="", help="YYYY-MM-DD (default today)")
    args=ap.parse_args()
    day = args.date or datetime.date.today().isoformat()
    # for dry-run without captures, inject minimal sample so brief is not empty
    posts = load_captures(day if not args.dry_run else None) if not args.dry_run else load_captures()
    # in dry-run, if no captures yet, use a tiny synthetic set so template renders
    if args.dry_run and not posts:
        posts=[
            {"text":"Need a website for my roofing business, site is slow","source":"r/smallbusiness","permalink":"https://example.com/a","score":40},
            {"text":"ai automation for my hvac company, manual invoicing killing us","source":"twitter:search","permalink":"https://example.com/b","score":35},
            {"text":"recruiting agency needs custom software and claude setup","source":"fb:search","permalink":"https://example.com/c","score":45},
        ]
        # still show youtube section as empty gracefully
        yt_briefs=[]
    else:
        if args.dry_run:
            yt_briefs=[]
        else:
            # only fetch youtube on real daily run; keep dry-run network-free
            try:
                yt_briefs = fetch_youtube_briefs()
            except Exception as e:
                print(f"[daily] youtube failed: {e}", file=sys.stderr)
                yt_briefs=[]
            # for non-dry real run with zero posts today, still render
            if not posts:
                print(f"[daily] no captures for {day} — rendering empty brief", file=sys.stderr)
    if 'yt_briefs' not in locals():
        yt_briefs=[]
    # when not dry-run and posts exist, ensure per_platform reflects today
    if not args.dry_run and posts and day:
        # filter to today for topics but keep all for brief if needed — we already filtered
        pass
    topics = top_topics(posts, k=10)
    per_platform = per_platform_blocks(posts)
    trend_posts = [] if args.dry_run else fetch_trend_tweets()
    md = render_daily(day, posts, topics, per_platform, yt_briefs, trend_posts=trend_posts)
    DATA.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(md)
    OUT_JSON.write_text(json.dumps({"day": day, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "post_count": len(posts), "topics": topics, "youtube": yt_briefs}, indent=2))
    print(md)
    print(f"\n[wrote {OUT_MD} + {OUT_JSON} | posts={len(posts)} topics={len(topics)} youtube={len(yt_briefs)}]", file=sys.stderr)
    # send to telegram unless dry-run
    if not args.dry_run:
        try:
            import telegram as tg
            tg.send_markdown(md)
            print("[daily] sent to telegram", file=sys.stderr)
        except Exception as e:
            print(f"[daily] telegram send failed: {e}", file=sys.stderr)
    return 0

if __name__ == "__main__":
    sys.exit(main())
