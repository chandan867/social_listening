#!/usr/bin/env python3
"""Weekly pain-theme digest — groups week's pain intents by icp+theme, verbatim quotes required."""
from __future__ import annotations
import collections, datetime, json, pathlib, sys
HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"
CAPTURE = DATA / "capture.jsonl"
OUT_MD = DATA / "weekly.md"

def load_week(days=7) -> list[dict]:
    if not CAPTURE.exists():
        return []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    out=[]
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        try: rec=json.loads(line)
        except Exception: continue
        # only pain intents with real icp
        io = rec.get("intent_obj",{}) or {}
        if io.get("intent")!="pain" or io.get("icp")=="not_icp" or not io.get("icp"):
            continue
        try:
            ts = datetime.datetime.fromisoformat(rec.get("captured_at","").replace("Z","+00:00"))
        except Exception:
            continue
        if ts < cutoff:
            continue
        out.append(rec)
    return out

def render(week: list[dict]) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines=[f"# Weekly Pain Themes — last 7 days  _(generated {now})_", f"_Pain intents with ICP: {len(week)}_", ""]
    if not week:
        lines+=["- _no pain themes this week (need LLM-classified captures to populate; dry-run samples are not classified)_","",
                "_Tip: run a live cycle with `bash run.sh` so capture.jsonl gains intent_obj fields._",""]
        return "\n".join(lines)
    buckets: dict[tuple[str,str], list[dict]] = collections.defaultdict(list)
    for rec in week:
        io = rec.get("intent_obj",{})
        icp = io.get("icp","other_local")
        # theme = first 6 words of one_line
        theme = (io.get("one_line","") or rec.get("text","")[:80]).strip().split()
        theme = " ".join(theme[:6]) or "general"
        buckets[(icp, theme)].append(rec)
    top = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
    for (icp, theme), recs in top:
        lines+=[f"## {icp} — {theme}  ({len(recs)})",""]
        for r in recs[:3]:
            quote = (r.get("intent_obj",{}).get("one_line") or r.get("text","")[:160]).strip().replace("\n"," ")
            link = r.get("permalink","")
            lines+=[f"> {quote}  — {link}" if link else f"> {quote}"]
        lines+=[""]
    lines+=["---", "_Next: pick 1 theme → write 1 post in the audience's exact words (FR-3.3: ≥50% pain-sourced)._"]
    return "\n".join(lines)

def main():
    week = load_week(7)
    md = render(week)
    DATA.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(md)
    print(md)
    print(f"\n[wrote {OUT_MD} | pain intents={len(week)}]", file=sys.stderr)
    return 0

if __name__=="__main__":
    sys.exit(main())
