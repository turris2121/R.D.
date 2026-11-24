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
import math
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
        r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", tries=1,
                params={"id": sid, "cosd": HISTORY_START.isoformat()})
        rows = list(csv.reader(io.StringIO(r.text)))[1:]
        pts = [[d, float(v)] for d, v in rows if v not in (".", "")]
        if not pts:
            raise RuntimeError(f"FRED {sid} returned no data")
        out[key] = {"id": sid, "points": pts}
    return out


TREASURY_COLS = {"tbill_1m": "1 Mo", "tbill_3m": "3 Mo", "tbill_6m": "6 Mo",
                 "tbill_1y": "1 Yr", "ust_2y": "2 Yr", "ust_10y": "10 Yr"}


def fetch_us() -> dict:
    """NY Fed (EFFR + target range, SOFR) and Treasury (yield curve). Both are the
    original publishers and answer cloud servers reliably; FRED is the fallback."""
    try:
        return fetch_us_official()
    except Exception as e:  # noqa: BLE001
        log(f"NY Fed / Treasury failed ({e}); trying FRED")
        return fetch_fred()


def fetch_us_official() -> dict:
    span = {"startDate": HISTORY_START.isoformat(), "endDate": TODAY.isoformat()}
    effr = get("https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json", params=span).json()["refRates"]
    sofr = get("https://markets.newyorkfed.org/api/rates/secured/sofr/search.json", params=span).json()["refRates"]
    effr.sort(key=lambda r: r["effectiveDate"])
    sofr.sort(key=lambda r: r["effectiveDate"])
    out = {
        "effr": {"id": "NY Fed EFFR", "points": [[r["effectiveDate"], float(r["percentRate"])] for r in effr]},
        "fed_upper": {"id": "NY Fed target", "points": [[r["effectiveDate"], float(r["targetRateTo"])] for r in effr]},
        "fed_lower": {"id": "NY Fed target", "points": [[r["effectiveDate"], float(r["targetRateFrom"])] for r in effr]},
        "sofr": {"id": "NY Fed SOFR", "points": [[r["effectiveDate"], float(r["percentRate"])] for r in sofr]},
    }
    rows = []
    for year in range(HISTORY_START.year, TODAY.year + 1):
        r = get(f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                f"daily-treasury-rates.csv/{year}/all",
                params={"type": "daily_treasury_yield_curve", "field_tdr_date_value": year, "_format": "csv"})
        rows += list(csv.DictReader(io.StringIO(r.text)))
    for key, col in TREASURY_COLS.items():
        pts = []
        for row in rows:
            v = (row.get(col) or "").strip()
            if not v:
                continue
            mm, dd, yy = row["Date"].split("/")
            pts.append([f"{yy}-{mm}-{dd}", float(v)])
        pts.sort()
        out[key] = {"id": f"Treasury {col}", "points": pts}
    for k, v in out.items():
        if not v["points"]:
            raise RuntimeError(f"{k}: no data")
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


def implied_odds(policy: float, bills: list[tuple[int, dict | None, float, float]],
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
    for h, obs, basis, iqr in bills:
        if obs is None or h <= k or (h - k) / h < 0.34:
            continue
        w = (h - k) / h
        excess = (obs["value"] - basis) - policy                   # pp over the bill's life
        implied_move = excess / w                                  # pp at the meeting
        # Weak = the reading sits inside the middle half of the bill's normal noise.
        # Mixed = a longer bill doesn't point the same way beyond its own noise.
        signal = "weak" if abs(excess) <= iqr / 2 else "clear"
        checks = []
        for h2, obs2, basis2, iqr2 in bills:
            if obs2 is None or h2 <= h:
                continue
            ex2 = (obs2["value"] - basis2) - policy
            checks.append({"days": h2, "excess_bp": round(ex2 * 100, 1), "noise_bp": round(iqr2 * 50, 1)})
            if signal == "clear" and (ex2 * excess < 0 or abs(ex2) <= iqr2 / 2):
                signal = "mixed"
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
            "policy_rate": policy, "basis": basis, "noise_iqr": iqr, "signal": signal,
            "excess_bp": round(excess * 100, 1), "longer_bills": checks,
            "implied_move_bp": round(implied_move * 100, 1),
            "p_hold": round(odds.get("hold", 0.0), 3),
            "p_cut": round(odds.get("cut", 0.0), 3),
            "p_hike": round(odds.get("hike", 0.0), 3),
            "p_50bp": round(big, 3),
        }
    return None


def empirical_basis(bill: dict | None, policy_pts: list, change_dates: list[str], h: int) -> tuple[float, float]:
    """Median of (bill yield - average policy rate actually in force over the
    bill's life) across the last 2 years. Comparing with what really happened,
    rather than with the rate on the day, removes the effect of expected moves,
    so what's left is the bill's usual spread. Works even when the bank moved often."""
    if not bill or not policy_pts:
        return 0.0, 0.0
    start = TODAY - dt.timedelta(days=730)
    spreads = []
    for d, y in bill["points"]:
        day = dt.date.fromisoformat(d)
        if day < start or day + dt.timedelta(days=h) > TODAY:
            continue
        vals = [value_on(policy_pts, (day + dt.timedelta(days=i)).isoformat()) for i in range(0, h, 7)]
        vals = [v for v in vals if v is not None]
        if vals:
            spreads.append(y - sum(vals) / len(vals))
    if len(spreads) < 10:
        warn(f"too little history to measure the usual spread for a {h}-day bill; assuming 0")
        return 0.0, 0.0
    spreads.sort()
    n = len(spreads)
    iqr = spreads[(3 * n) // 4] - spreads[n // 4]
    return round(spreads[n // 2], 3), round(iqr, 3)


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


FX_HORIZONS = [("1 month", 30, "cetes_28", "tbill_1m"), ("3 months", 91, "cetes_91", "tbill_3m"),
               ("6 months", 182, "cetes_182", "tbill_6m"), ("1 year", 364, "cetes_364", "tbill_1y")]


def fx_outlook(fix: dict | None, bmx: dict, us: dict, elections: list[dict]) -> dict | None:
    """USD/MXN odds per horizon.

    Centre: the forward rate (spot adjusted for the CETES vs T-bill gap), which is
    what the market prices in, not a forecast. Width: the last year's realized
    volatility of the FIX. Model: lognormal around the forward.
    """
    if not fix or len(fix.get("points", [])) < 200:
        return None
    pts = fix["points"]
    spot, spot_date = pts[-1][1], pts[-1][0]
    rets = [math.log(b[1] / a[1]) for a, b in zip(pts[-253:-1], pts[-252:])]
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) * 252)
    out = []
    for label, days, mx_key, us_key in FX_HORIZONS:
        mx, usr = latest(bmx.get(mx_key)), latest(us.get(us_key))
        if not mx or not usr:
            continue
        fwd = spot * (1 + mx["value"] / 100 * days / 360) / (1 + usr["value"] / 100 * days / 360)
        t = days / 365
        sd = vol * math.sqrt(t)
        mu = math.log(fwd / spot) - sd * sd / 2
        z = 1.2816
        horizon_end = (TODAY + dt.timedelta(days=days)).isoformat()
        out.append({
            "label": label, "days": days, "forward": round(fwd, 4),
            "forward_pct": round((fwd / spot - 1) * 100, 2),
            "p_usd_up": round(1 - norm_cdf((0 - mu) / sd), 3),
            "p_up_5": round(1 - norm_cdf((math.log(1.05) - mu) / sd), 3),
            "p_down_5": round(norm_cdf((math.log(0.95) - mu) / sd), 3),
            "range80": [round(spot * math.exp(mu - z * sd), 2), round(spot * math.exp(mu + z * sd), 2)],
            "mx_rate": mx["value"], "us_rate": usr["value"],
            "events": [e["name"] for e in elections if TODAY.isoformat() <= e["date"] <= horizon_end],
        })
    return {"spot": spot, "spot_date": spot_date, "vol_annual": round(vol * 100, 2), "horizons": out}


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
    fred, fred_at = cached("us", fetch_us)
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
            basis, iqr = empirical_basis(b, policy_pts, change_dates, h)
            if override is not None:
                basis = override
            out.append((h, latest(b), basis, iqr))
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
        pair("2 years", "ust_2y", "cetes_728", fred, bmx, "Treasury 2Y", "CETES 728d"),
        pair("10 years", "ust_10y", "bono_10y", fred, bmx, "Treasury 10Y", "Bono M 10Y"),
    ]

    # --- tasa actual ----------------------------------------------------------
    # Mexico vs US yield for each term, as published (no averaging).
    tasa = []
    for label, mx_key, mx_name, us_key, us_name in (
            ("1 month", "cetes_28", "CETES 28d", "tbill_1m", "T-bill 1M"),
            ("3 months", "cetes_91", "CETES 91d", "tbill_3m", "T-bill 3M"),
            ("6 months", "cetes_182", "CETES 182d", "tbill_6m", "T-bill 6M"),
            ("1 year", "cetes_364", "CETES 364d", "tbill_1y", "Treasury 1Y"),
            ("2 years", "cetes_728", "CETES 728d", "ust_2y", "Treasury 2Y"),
            ("10 years", "bono_10y", "Bono M 10Y", "ust_10y", "Treasury 10Y")):
        tasa.append({"label": label, "mx_name": mx_name, "mx": latest(bmx.get(mx_key)),
                     "us_name": us_name, "us": latest(fred.get(us_key))})
    bx_chg = changes(bx_pts)

    elections = sorted(CONFIG.get("elections", []), key=lambda e: e["date"])
    for e in elections:
        e["days_until"] = (dt.date.fromisoformat(e["date"]) - TODAY).days
    fx = fx_outlook(bmx.get("usdmxn_fix"), bmx, fred, elections)

    out = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "today": TODAY.isoformat(),
        "sources_fetched": {"us": fred_at, "banxico": bmx_at, "fomc_calendar": fomc_at,
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
        "tasa_actual": tasa,
        "fx": fx,
        "elections": elections,
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
