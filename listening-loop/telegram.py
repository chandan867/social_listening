#!/usr/bin/env python3
"""Send data/digest.md via Telegram Bot API. Skips gracefully if creds missing."""
from __future__ import annotations
import os, sys
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
MD = DATA / "digest.md"
CSV = DATA / "leads.csv"

def load_env():
    for p in [HERE / ".env", HERE.parent / ".env", HERE.parent / "Social-ops" / ".env"]:
        if p.exists():
            for line in p.read_text().splitlines():
                line=line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k,v=line.split("=",1)
                k=k.strip(); v=v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k]=v

def chunk(s, n=4096):
    out=[]; cur=""
    for line in s.splitlines(True):
        if len(cur)+len(line) > n:
            out.append(cur); cur=line
        else:
            cur+=line
    if cur: out.append(cur)
    final=[]
    for c in out:
        while len(c)>n:
            final.append(c[:n]); c=c[n:]
        if c: final.append(c)
    return final

def _creds():
    load_env()
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("CHAT_ID") or "").strip()
    return token, chat_id

def send_markdown(text: str) -> bool:
    import requests
    token, chat_id = _creds()
    if not token or not chat_id:
        print("[telegram] skipping — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.", file=sys.stderr)
        return False
    if not text.strip():
        print("[telegram] empty text — nothing to send.", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok=True
    for i, part in enumerate(chunk(text)):
        try:
            r = requests.post(url, json={"chat_id": chat_id, "text": part, "parse_mode":"Markdown", "disable_web_page_preview": True}, timeout=15)
            if not r.ok and "can't parse" in r.text.lower():
                r = requests.post(url, json={"chat_id": chat_id, "text": part}, timeout=15)
            if not r.ok:
                print(f"[telegram] chunk {i} failed: {r.status_code} {r.text[:300]}", file=sys.stderr)
                ok=False
            else:
                print(f"[telegram] sent chunk {i+1}/{len(chunk(text))} ok", file=sys.stderr)
        except Exception as e:
            print(f"[telegram] request failed: {e}", file=sys.stderr)
            ok=False
    return ok

def send_document(path: Path, caption: str = "") -> bool:
    import requests
    token, chat_id = _creds()
    if not token or not chat_id:
        print("[telegram] skipping document — creds missing.", file=sys.stderr)
        return False
    if not path.exists():
        print(f"[telegram] document not found: {path}", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(path, "rb") as f:
            r = requests.post(url, data={"chat_id": chat_id, "caption": caption[:1024]}, files={"document": (path.name, f, "text/csv")}, timeout=30)
        if not r.ok:
            print(f"[telegram] sendDocument failed: {r.status_code} {r.text[:500]}", file=sys.stderr)
            return False
        print(f"[telegram] sent document {path.name} ({caption}) ok", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[telegram] sendDocument error: {e}", file=sys.stderr)
        return False

def main():
    load_env()
    import requests
    token, chat_id = _creds()
    if not token or not chat_id:
        print("[telegram] skipping — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set (see SETUP.md).", file=sys.stderr)
        return 0
    if not MD.exists():
        print(f"[telegram] no digest at {MD} — run digest.py first.", file=sys.stderr)
        return 0
    text = MD.read_text(encoding="utf-8")
    if not text.strip():
        print("[telegram] digest empty — nothing to send.", file=sys.stderr)
        return 0
    ok = send_markdown(text)
    # CSV delivery: if leads.csv exists send it, else note 0
    if CSV.exists() and CSV.stat().st_size > 0:
        # count qualified rows (exclude header)
        try:
            n = max(0, len(CSV.read_text().splitlines()) - 1)
        except Exception:
            n = 0
        if n > 0:
            send_document(CSV, f"{n} qualified leads this cycle")
        else:
            send_markdown("0 qualified leads this cycle")
    else:
        # check if digest just ran and produced 0 — still notify
        send_markdown("0 qualified leads this cycle")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
