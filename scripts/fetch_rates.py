#!/usr/bin/env python3
"""Fetch US and Mexico rates, meeting calendars and implied move odds.

Writes site/data/rates.json. Every source is cached in data/raw/; if a fetch
fails the previous cache is used and the page shows a warning.

Usage:  python scripts/fetch_rates.py
Env:    BANXICO_TOKEN  (free: https://www.banxico.org.mx/SieAPIRest/service/v1/token)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "site" / "data"
CONFIG = json.loads((ROOT / "config.json").read_text())

UA = "Mozilla/5.0 (compatible; rates-dashboard/1.0; open-data dashboard)"
S = requests.Session()
S.headers["User-Agent"] = UA

TODAY = dt.date.today()
HISTORY_START = TODAY - dt.timedelta(days=365 * 4)
WARNINGS: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)


def warn(msg: str) -> None:
    log("WARNING: " + msg)
    WARNINGS.append(msg)


def get(url: str, tries: int = 3, **kw) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = S.get(url, timeout=60, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"{url}: {last}")


def cached(name: str, fetch):
    """Run fetch(); store the result. On failure fall back to the stored copy."""
    path = RAW / f"{name}.json"
    try:
        data = fetch()
        RAW.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched": TODAY.isoformat(), "data": data}, ensure_ascii=False))
        return data, TODAY.isoformat()
    except Exception as e:  # noqa: BLE001 - any failure falls back to cache
        if path.exists():
            old = json.loads(path.read_text())
            warn(f"{name}: fetch failed ({e}); using copy from {old['fetched']}")
            return old["data"], old["fetched"]
        warn(f"{name}: fetch failed ({e}) and no cached copy")
        return None, None


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def fetch_fred() -> dict:
    """FRED's public CSV endpoint (no key). One request per series."""
    out = {}
    for key, sid in CONFIG["fred_series"].items():
        r = get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                params={"id": sid, "cosd": HISTORY_START.isoformat()})
        rows = list(csv.reader(io.StringIO(r.text)))[1:]
        pts = [[d, float(v)] for d, v in rows if v not in (".", "")]
        if not pts:
            raise RuntimeError(f"FRED {sid} returned no data")
        out[key] = {"id": sid, "points": pts}
    return out


def fetch_banxico() -> dict:
    token = os.environ.get("BANXICO_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BANXICO_TOKEN is not set")
    series = CONFIG["banxico_series"]
    ids = ",".join(s["id"] for s in series.values())
    url = (f"https://www.banxico.org.mx/SieAPIRest/service/v1/series/{ids}/datos/"
           f"{HISTORY_START.isoformat()}/{TODAY.isoformat()}")
    j = get(url, headers={"Bmx-Token": token, "Accept": "application/json"}).json()
    by_id = {s["idSerie"]: s for s in j["bmx"]["series"]}
    out = {}
    for key, spec in series.items():
        s = by_id.get(spec["id"])
        if not s:
            warn(f"Banxico series {spec['id']} ({key}) missing from response")
            continue
        pts = []
        for d in s.get("datos") or []:
            v = d["dato"].replace(",", "")
            if v in ("N/E", ""):
                continue
            dd, mm, yy = d["fecha"].split("/")
            pts.append([f"{yy}-{mm}-{dd}", float(v)])
        out[key] = {"id": spec["id"], "title": s.get("titulo", ""), "points": pts}
    return out


MONTHS_EN = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MONTHS_ES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], 1)}


def fetch_fomc() -> list[dict]:
    """Parse federalreserve.gov's FOMC calendar. Decision day = last meeting day."""
    t = get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").text
    parts = re.split(r"(\d{4}) FOMC Meetings", t)
    meetings = []
    for i in range(1, len(parts) - 1, 2):
        year, body = int(parts[i]), parts[i + 1]
        pairs = re.findall(
            r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<',
            body, flags=re.S)
        for month_txt, date_txt in pairs:
            months = [MONTHS_EN[m[:3].lower()] for m in re.findall(r"[A-Za-z]+", month_txt)
                      if m[:3].lower() in MONTHS_EN]
            days = [int(x) for x in re.findall(r"\d+", date_txt)]
            if not months or not days:
                continue
            m = months[-1]
            y = year + 1 if (len(months) > 1 and months[-1] < months[0]) else year
            meetings.append({
                "date": dt.date(y, m, days[-1]).isoformat(),
                "label": f"{month_txt.strip()} {date_txt.strip()}",
                "sep": "*" in date_txt,
                "unscheduled": "unscheduled" in date_txt.lower(),
            })
    if len(meetings) < 8:
        raise RuntimeError(f"only {len(meetings)} FOMC meetings parsed; page layout may have changed")
    return sorted({m["date"]: m for m in meetings}.values(), key=lambda m: m["date"])


def parse_banxico_calendar_pdf(text: str) -> tuple[int, list[str]]:
    """Banxico's calendar PDF lists announcements, minutes and reports in loose
    columns with footnote digits glued on. An announcement is the date whose
    minutes follow exactly 14 days later (December's minutes land in January)."""
    ym = re.search(r"Calendario\s+(\d{4})", text)
    if not ym:
        raise RuntimeError("year not found in calendar PDF")
    year = int(ym.group(1))
    cands: set[dt.date] = set()
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-zé]+)\s+([\d ]+)\s*$", line)
        if not m or m.group(1).lower() not in MONTHS_ES:
            continue
        month = MONTHS_ES[m.group(1).lower()]
        for tok in m.group(2).split():
            n = int(tok)
            while n > 31:          # strip a glued-on footnote marker
                n //= 10
            try:
                cands.add(dt.date(year, month, n))
            except ValueError:
                pass
    week2 = dt.timedelta(days=14)
    ann = sorted(d for d in cands if d + week2 in cands and d.weekday() == 3)
    minutes = {d + week2 for d in ann}
    ann += [d for d in cands if d.month == 12 and d.weekday() == 3 and d not in minutes and d not in ann]
    ann = sorted(ann)
    if not 6 <= len(ann) <= 10:
        raise RuntimeError(f"parsed {len(ann)} announcement dates; expected about 8")
    return year, [d.isoformat() for d in ann]


def fetch_banxico_calendar() -> dict:
    from pypdf import PdfReader  # imported here so the rest runs without it

    page = get("https://www.banxico.org.mx/publicaciones-y-prensa/"
               "anuncios-de-las-decisiones-de-politica-monetaria/"
               "anuncios-politica-monetaria-t.html").text
    links = re.findall(r'href="([^"]+\.pdf)"[^>]*data-title="Calendario[^"]*"', page)
    if not links:
        raise RuntimeError("calendar PDF link not found on Banxico page")
    # Keep previously parsed years so past meetings stay listed after the link moves on.
    old = RAW / "banxico_calendar.json"
    years = json.loads(old.read_text())["data"] if old.exists() else {}
    for href in dict.fromkeys(links):
        url = href if href.startswith("http") else "https://www.banxico.org.mx" + href
        pdf = get(url.replace("{", "%7B").replace("}", "%7D")).content
        text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
        year, dates = parse_banxico_calendar_pdf(text)
        years[str(year)] = dates
    return years


# --------------------------------------------------------------------------- #
# Derived numbers
# --------------------------------------------------------------------------- #
def latest(series: dict | None):
    if not series or not series.get("points"):
        return None
    d, v = series["points"][-1]
    return {"date": d, "value": v}


def value_on(points: list, day: str):
    """Last value on or before `day`."""
    v = None
    for d, x in points:
        if d > day:
            break
        v = x
    return v


def changes(points: list) -> list[dict]:
    out, prev = [], None
    for d, v in points:
        if prev is not None and abs(v - prev) > 1e-9:
            out.append({"date": d, "from": prev, "to": v, "bp": round((v - prev) * 100)})
        prev = v
    return out


def step_series(points: list) -> list:
    """Keep only the points where the value changes (plus the last one)."""
    out = []
    for d, v in points:
        if not out or abs(out[-1][1] - v) > 1e-9:
            out.append([d, v])
    if points and out[-1][0] != points[-1][0]:
        out.append(points[-1])
    return out


def decisions(meeting_dates: list[str], points: list) -> list[dict]:
    """For each past meeting, compare the rate the day before with 5 days after."""
    out = []
    for d in meeting_dates:
        day = dt.date.fromisoformat(d)
        if day >= TODAY:
            continue
        before = value_on(points, (day - dt.timedelta(days=1)).isoformat())
        after = value_on(points, (day + dt.timedelta(days=5)).isoformat())
        if before is None or after is None:
            continue
        bp = round((after - before) * 100)
        out.append({"date": d, "bp": bp, "rate": after,
                    "action": "hike" if bp > 0 else "cut" if bp < 0 else "hold"})
    return out


def implied_odds(policy: float, bills: list[tuple[int, dict | None, float]],
                 next_meeting: str | None) -> dict | None:
    """Estimate move odds at the next meeting from short bill yields.

    If the bank moves by `step` at a meeting k days from now, the average rate
    over the next h days changes by step*(h-k)/h. So the bill yield (minus its
    usual basis) tells us the probability-weighted move.
    """
    if next_meeting is None:
        return None
    step = CONFIG["probability"]["step_pp"]
    k = (dt.date.fromisoformat(next_meeting) - TODAY).days
    for h, obs, basis in bills:
        if obs is None or h <= k or (h - k) / h < 0.34:
            continue
        w = (h - k) / h
        implied_move = ((obs["value"] - basis) - policy) / w      # pp at the meeting
        p = implied_move / step                                    # in 25 bp moves
        a = abs(p)
        direction = "hike" if p > 0 else "cut"
        if a <= 1:
            odds = {"hold": 1 - a, direction: a}
            big = 0.0
        else:
            big = min(a - 1, 1.0)
            odds = {"hold": 0.0, direction: 1.0}
        return {
            "meeting": next_meeting, "days_to_meeting": k,
            "instrument_days": h, "instrument_yield": obs["value"], "instrument_date": obs["date"],
            "policy_rate": policy, "basis": basis,
            "implied_move_bp": round(implied_move * 100, 1),
            "p_hold": round(odds.get("hold", 0.0), 3),
            "p_cut": round(odds.get("cut", 0.0), 3),
            "p_hike": round(odds.get("hike", 0.0), 3),
            "p_50bp": round(big, 3),
        }
    return None


def empirical_basis(bill: dict | None, policy_pts: list, change_dates: list[str], h: int) -> float:
    """Median (bill - policy rate) over the last 2 years, using only days where
    the policy rate then stayed unchanged for the bill's whole life. That is the
    spread the bill carries when no move is coming."""
    if not bill or not policy_pts:
        return 0.0
    start = (TODAY - dt.timedelta(days=730)).isoformat()
    spreads = []
    for d, y in bill["points"]:
        day = dt.date.fromisoformat(d)
        end = (day + dt.timedelta(days=h)).isoformat()
        if d < start or end > TODAY.isoformat():
            continue
        if any(d < c <= end for c in change_dates):
            continue
        p = value_on(policy_pts, d)
        if p is not None:
            spreads.append(y - p)
    if len(spreads) < 20:
        return 0.0
    spreads.sort()
    return round(spreads[len(spreads) // 2], 3)


def next_after(dates: list[str]) -> str | None:
    return next((d for d in dates if d >= TODAY.isoformat()), None)


def pair(label, us_key, mx_key, us, mx, us_name, mx_name):
    u, m = latest(us.get(us_key)), latest(mx.get(mx_key))
    row = {"label": label, "us_name": us_name, "mx_name": mx_name, "us": u, "mx": m,
           "spread_pp": None, "relative_pct": None}
    if u and m:
        row["spread_pp"] = round(m["value"] - u["value"], 2)
        if u["value"]:
            row["relative_pct"] = round((m["value"] - u["value"]) / u["value"] * 100, 1)
    return row


# --------------------------------------------------------------------------- #
def main() -> int:
    fred, fred_at = cached("fred", fetch_fred)
    bmx, bmx_at = cached("banxico", fetch_banxico)
    fomc, fomc_at = cached("fomc_calendar", fetch_fomc)
    bxcal, bxcal_at = cached("banxico_calendar", fetch_banxico_calendar)
    fred, bmx = fred or {}, bmx or {}

    # Flag Banxico series whose title doesn't look like what we expect.
    for key, spec in CONFIG["banxico_series"].items():
        s = bmx.get(key)
        if s and spec["expect"].lower() not in s.get("title", "").lower():
            warn(f"Banxico series {spec['id']} ({key}) is titled '{s['title']}'; check the ID in config.json")

    # --- calendars ---------------------------------------------------------
    fed_dates = sorted({m["date"] for m in (fomc or [])} |
                       {d for v in CONFIG["fed_calendar_manual"].values() for d in v})
    bx_years = dict(CONFIG["banxico_calendar_manual"])
    bx_years.update(bxcal or {})
    bx_dates = sorted({d for v in bx_years.values() for d in v})
    if str(TODAY.year + 1) not in bx_years and TODAY.month >= 12:
        warn(f"Banxico's {TODAY.year + 1} calendar isn't published or parsed yet")

    # --- policy rates ------------------------------------------------------
    fed_up, fed_lo = latest(fred.get("fed_upper")), latest(fred.get("fed_lower"))
    effr = latest(fred.get("effr"))
    bx_target = latest(bmx.get("target"))

    fed_pts = fred.get("fed_upper", {}).get("points", [])
    bx_pts = bmx.get("target", {}).get("points", [])

    prob = CONFIG["probability"]

    def bills(src, policy_pts, change_dates, spec, override):
        out = []
        for h, key in spec:
            b = src.get(key)
            basis = override if override is not None else empirical_basis(b, policy_pts, change_dates, h)
            out.append((h, latest(b), basis))
        return out

    fed_chg_dates = [c["date"] for c in changes(fed_pts)]
    bx_chg_dates = [c["date"] for c in changes(bx_pts)]
    effr_pts = fred.get("effr", {}).get("points", [])
    fed_odds = None
    if effr:
        fed_odds = implied_odds(effr["value"],
                                bills(fred, effr_pts, fed_chg_dates,
                                      [(30, "tbill_1m"), (91, "tbill_3m"), (182, "tbill_6m")],
                                      prob["fed_basis_pp"]),
                                next_after(fed_dates))
    bx_odds = None
    if bx_target:
        bx_odds = implied_odds(bx_target["value"],
                               bills(bmx, bx_pts, bx_chg_dates,
                                     [(28, "cetes_28"), (91, "cetes_91"), (182, "cetes_182")],
                                     prob["banxico_basis_pp"]),
                               next_after(bx_dates))
    for name, o in (("Fed", fed_odds), ("Banxico", bx_odds)):
        if o and (TODAY - dt.date.fromisoformat(o["instrument_date"])).days > 10:
            warn(f"{name} odds use a yield from {o['instrument_date']}, more than 10 days old")

    # --- investments table ---------------------------------------------------
    rows = [
        pair("Policy rate", "fed_upper", "target", fred, bmx, "Fed funds (upper bound)", "Banxico target"),
        pair("Overnight", "effr", "tiie_fondeo", fred, bmx, "Effective fed funds", "TIIE de fondeo"),
        pair("Overnight (secured)", "sofr", "tiie_fondeo", fred, bmx, "SOFR", "TIIE de fondeo"),
        pair("~1 month", "tbill_1m", "cetes_28", fred, bmx, "T-bill 1M", "CETES 28d"),
        pair("~1 month (interbank)", "tbill_1m", "tiie_28", fred, bmx, "T-bill 1M", "TIIE 28d"),
        pair("3 months", "tbill_3m", "cetes_91", fred, bmx, "T-bill 3M", "CETES 91d"),
        pair("6 months", "tbill_6m", "cetes_182", fred, bmx, "T-bill 6M", "CETES 182d"),
        pair("1 year", "tbill_1y", "cetes_364", fred, bmx, "Treasury 1Y", "CETES 364d"),
        pair("10 years", "ust_10y", "bono_10y", fred, bmx, "Treasury 10Y", "Bono M 10Y"),
    ]

    # --- tasa objetivo -------------------------------------------------------
    gbm = CONFIG["gbm_smart_cash"]
    tasa = None
    if gbm.get("rate") is not None and bx_target:
        tasa = round((float(gbm["rate"]) + bx_target["value"]) / 2, 3)
    if gbm.get("rate") is None:
        warn("GBM Smart Cash rate is not set, so Tasa objetivo can't be computed")
    elif gbm.get("as_of") and (TODAY - dt.date.fromisoformat(gbm["as_of"])).days > 21:
        warn(f"GBM Smart Cash rate was last set on {gbm['as_of']}; check it's still current")
    bx_chg = changes(bx_pts)
    if gbm.get("as_of") and bx_chg and bx_chg[-1]["date"] > gbm["as_of"]:
        warn("Banxico changed its rate after the GBM Smart Cash rate was last set; GBM has probably changed too")

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "today": TODAY.isoformat(),
        "sources_fetched": {"fred": fred_at, "banxico": bmx_at, "fomc_calendar": fomc_at,
                            "banxico_calendar": bxcal_at},
        "fed": {
            "upper": fed_up, "lower": fed_lo, "effr": effr,
            "meetings": [m for m in (fomc or [])],
            "next_meeting": next_after(fed_dates),
            "decisions": decisions(fed_dates, fed_pts)[-12:],
            "changes": changes(fed_pts)[-12:],
            "odds": fed_odds,
        },
        "banxico": {
            "target": bx_target,
            "meetings": [{"date": d} for d in bx_dates],
            "next_meeting": next_after(bx_dates),
            "decisions": decisions(bx_dates, bx_pts)[-12:],
            "changes": bx_chg[-12:],
            "odds": bx_odds,
            "usdmxn": latest(bmx.get("usdmxn_fix")),
            "series_titles": {k: v.get("title") for k, v in bmx.items()},
        },
        "investments": rows,
        "gbm_smart_cash": {"rate": gbm.get("rate"), "as_of": gbm.get("as_of")},
        "tasa_objetivo": tasa,
        "history": {
            "fed_upper": step_series(fed_pts),
            "fed_lower": step_series(fred.get("fed_lower", {}).get("points", [])),
            "banxico_target": step_series(bx_pts),
        },
        "warnings": WARNINGS,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    log(f"Wrote {OUT / 'rates.json'} with {len(WARNINGS)} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
