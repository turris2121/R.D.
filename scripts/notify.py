#!/usr/bin/env python3
"""Send the weekly rates summary to Telegram.

Env:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DASHBOARD_URL (optional)
      python scripts/notify.py --chat-id   # prints chat ids that messaged the bot
      python scripts/notify.py --dry-run   # prints the message instead of sending
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
D = json.loads((ROOT / "site" / "data" / "rates.json").read_text())


def pct(v, digits=2):
    return "n/a" if v is None else f"{v:.{digits}f}%"


def val(o, digits=2):
    return pct(o["value"], digits) if o else "n/a"


def day(iso):
    if not iso:
        return "n/a"
    d = dt.date.fromisoformat(iso)
    return f"{d:%a %d %b %Y} (in {(d - dt.date.fromisoformat(D['today'])).days} days)"


def odds_line(o):
    if not o:
        return "Odds: not enough data"
    parts = []
    for k, name in (("p_cut", "cut"), ("p_hold", "hold"), ("p_hike", "hike")):
        if o[k] > 0.005:
            parts.append(f"{name} {o[k]:.0%}")
    s = "Odds (est.): " + ", ".join(parts)
    if o["p_50bp"] > 0.005:
        s += " (more than one 25 bp step priced)"
    s += f"  [implied {o['implied_move_bp']:+.0f} bp]"
    s += {"clear": "\nSignal: clear (all bill maturities agree)",
          "mixed": "\nSignal: mixed (longer bills disagree), low confidence",
          "weak": "\nSignal: weak (within normal noise), read as a hold"}.get(o.get("signal"), "")
    return s


def last_decision(bank):
    d = bank["decisions"][-1] if bank["decisions"] else None
    if not d:
        return "n/a"
    return f"{d['action']} {d['bp']:+d} bp on {d['date']}" if d["bp"] else f"hold on {d['date']}"


def build() -> str:
    f, b = D["fed"], D["banxico"]
    e = lambda x: html.escape(x, quote=False)
    rng = f"{val(f['lower'])}–{val(f['upper'])}" if f["upper"] else "n/a"
    lines = [
        f"<b>Weekly rates · {D['today']}</b>",
        "",
        f"🇺🇸 <b>Fed</b> {rng} (EFFR {val(f['effr'])})",
        f"Last: {last_decision(f)}",
        f"Next: {day(f['next_meeting'])}",
        html.escape(odds_line(f["odds"]), quote=False),
        "",
        f"🇲🇽 <b>Banxico</b> {val(b['target'])}",
        f"Last: {last_decision(b)}",
        f"Next: {day(b['next_meeting'])}",
        html.escape(odds_line(b["odds"]), quote=False),
        "",
        "<b>Mexico − US</b>",
    ]
    for r in D["investments"]:
        if r["spread_pp"] is None or "(" in r["label"]:
            continue
        lines.append(f"{e(r['label'])}: {r['mx']['value']:.2f}% vs {r['us']['value']:.2f}% "
                     f"→ {r['spread_pp']:+.2f} pp ({r['relative_pct']:+.0f}%)")
    g = D["gbm_smart_cash"]
    lines += ["",
              f"<b>Tasa objetivo: {pct(D['tasa_objetivo'], 3)}</b>",
              f"= mean of GBM Smart Cash {pct(g['rate'])} (as of {g['as_of'] or 'never'}) "
              f"and Banxico {val(b['target'])}"]
    n = D.get("nu_cajitas") or {}
    if n.get("rate") is not None:
        lines.append(f"Nu Cajitas (instant access): {pct(n['rate'])} (as of {n['as_of']})")
    if b.get("usdmxn"):
        lines.append(f"USD/MXN FIX: {b['usdmxn']['value']:.4f}")
    if D["warnings"]:
        lines += ["", "⚠️ " + "\n⚠️ ".join(e(w) for w in D["warnings"][:6])]
    url = os.environ.get("DASHBOARD_URL")
    if url:
        lines += ["", f'<a href="{e(url)}">Open dashboard</a>']
    return "\n".join(lines)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if "--chat-id" in sys.argv:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or
                 u["message"]["chat"].get("title") for u in r.get("result", []) if "message" in u}
        print(chats or "No messages yet. Send your bot any message in Telegram, then rerun.")
        return 0
    msg = build()
    if "--dry-run" in sys.argv:
        print(msg)
        return 0
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping notification")
        return 0
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                      json={"chat_id": chat, "text": msg, "parse_mode": "HTML",
                            "disable_web_page_preview": True})
    if not r.ok:
        print(f"Telegram error {r.status_code}: {r.text}")
        return 1
    print("Sent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
