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


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
ACT = {"cut": "Baja", "hold": "Se mantiene", "hike": "Sube"}
FLAG = {"MX": "🇲🇽", "US": "🇺🇸"}


def pct(v, digits=2):
    return "s/d" if v is None else f"{v:.{digits}f}%"


def val(o, digits=2):
    return pct(o["value"], digits) if o else "s/d"


def fecha(iso, weekday=False):
    if not iso:
        return "s/d"
    d = dt.date.fromisoformat(iso)
    s = f"{d.day} {MESES[d.month - 1]} {d.year}"
    return f"{DIAS[d.weekday()]} {s}" if weekday else s


def dias(iso):
    return (dt.date.fromisoformat(iso) - dt.date.fromisoformat(D["today"])).days


def build() -> str:
    e = lambda x: html.escape(x, quote=False)
    banks = (("🇺🇸 Fed", D["fed"]), ("🇲🇽 Banxico", D["banxico"]))
    lines = [f"<b>Visión del futuro · {fecha(D['today'])}</b>", "", "<b>1) Posible fecha de cambio</b>"]
    for name, B in banks:
        n = B["next_meeting"]
        lines.append(f"{name}: {fecha(n, True)} (en {dias(n)} días)" if n else f"{name}: s/d")

    lines += ["", "<b>2) Posibilidad de cambio de tasa</b>"]
    sig = {"clear": "señal clara", "mixed": "señal mixta, confianza baja", "weak": "señal débil, léelo como «se mantiene»"}
    for name, B in banks:
        o = B["odds"]
        if not o:
            lines.append(f"{name}: sin datos")
            continue
        lines.append(f"{name}: baja {o['p_cut']:.0%} · se mantiene {o['p_hold']:.0%} · sube {o['p_hike']:.0%} "
                     f"({sig.get(o['signal'], '')})")

    lines += ["", "<b>3) Tasa actual</b>  🇲🇽 vs 🇺🇸"]
    for t in D["tasa_actual"]:
        lines.append(f"{t['label']}: <b>{val(t['mx'])}</b> vs <b>{val(t['us'])}</b>")

    fx, efx = D.get("fx"), D.get("election_fx")
    lines += ["", "<b>4) Probabilidad tipo de cambio</b>"]
    if fx:
        lines.append(f"Dólar hoy (FIX): ${fx['spot']:.2f}")
    if efx:
        for x in efx["elections"][:2]:
            w = x["week"]
            lines.append(f"{FLAG[x['country']]} {e(x['name'])}, {fecha(x['date'])} (en {x['days_until']} días): "
                         f"movimiento ≥5% {w['p_big5']:.0%} (normal {w['p_big5_normal']:.0%}); "
                         f"dólar ↑3% {w['p_weaker3']:.0%}, ↓3% {w['p_stronger3']:.0%}; "
                         f"mercado ${x['forward']:.2f}")
    if D["warnings"]:
        lines += ["", "⚠️ " + "\n⚠️ ".join(e(w) for w in D["warnings"][:6])]
    url = os.environ.get("DASHBOARD_URL")
    if url:
        lines += ["", f'<a href="{e(url)}">Abrir el tablero</a>']
    return "\n".join(lines)


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if "--chat-id" in sys.argv:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name") or
                 u["message"]["chat"].get("title") for u in r.get("result", []) if "message" in u}
        print(chats or "Aún no hay mensajes. Escríbele cualquier cosa a tu bot en Telegram y vuelve a correr esto.")
        return 0
    msg = build()
    if "--dry-run" in sys.argv:
        print(msg)
        return 0
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID; no se envía la notificación")
        return 0
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                      json={"chat_id": chat, "text": msg, "parse_mode": "HTML",
                            "disable_web_page_preview": True})
    if not r.ok:
        print(f"Error de Telegram {r.status_code}: {r.text}")
        return 1
    print("Enviado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
