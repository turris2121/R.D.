# Fed & Banxico Rates Dashboard

A self-updating dashboard comparing US and Mexico interest rates:

- **A. Calendars:** every FOMC (Fed) and Banxico decision date, with past decisions marked cut / hold / hike.
- **B. Investment rates:** policy rate, overnight, 1M, 3M, 6M, 1Y and 10Y, US vs Mexico, with the spread in percentage points and in relative %.
- **C. Odds** of a cut, hold or hike at each bank's next meeting, estimated from bill yields.
- **D. Tasa objetivo** = (GBM Smart Cash rate + Banxico target) ÷ 2.

Every day at 13:00 UTC (07:00 Mexico City) a GitHub Action runs `scripts/fetch_rates.py`, commits the new data and redeploys the site to GitHub Pages. **On Mondays it also sends the summary to Telegram.**

## Data sources

| Source | Provides | Key needed |
|---|---|---|
| [New York Fed](https://markets.newyorkfed.org/static/docs/markets-api.html) | Fed target range, EFFR, SOFR | no |
| [US Treasury](https://home.treasury.gov/resource-center/data-chart-center/interest-rates) | T-bill and Treasury yields (1M–10Y) | no |
| [FRED](https://fred.stlouisfed.org) | fallback for both of the above | no |
| [federalreserve.gov](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm) | FOMC meeting calendar | no |
| [Banxico SIE API](https://www.banxico.org.mx/SieAPIRest/service/v1/) | target rate, TIIE, CETES, Bono M 10Y, USD/MXN FIX | free token |
| [Banxico calendar PDF](https://www.banxico.org.mx/publicaciones-y-prensa/anuncios-de-las-decisiones-de-politica-monetaria/anuncios-politica-monetaria-t.html) | Banxico decision dates | no |
| GBM Smart Cash | entered by you (GBM has no public API) | — |

If a source fails, the previous copy in `data/raw/` is used and the page and Telegram message show a warning.

## Setup (one time, ~10 minutes)

1. **Banxico token.** Go to <https://www.banxico.org.mx/SieAPIRest/service/v1/token>, fill in your email and solve the captcha. The token appears on screen.
2. **Telegram bot.**
   1. In Telegram, message **@BotFather**, send `/newbot` and follow the prompts. It gives you a **bot token**.
   2. Open a chat with your new bot and send it any message (e.g. "hi").
   3. Find your **chat id**: on your Mac run
      ```sh
      cd ~/rates-dashboard
      TELEGRAM_BOT_TOKEN=<bot token> python3 scripts/notify.py --chat-id
      ```
      It prints something like `{123456789: 'Your name'}`; the number is the chat id.
3. **Create the GitHub repo** (empty, no README) named `rates-dashboard` at <https://github.com/new>, then push:
   ```sh
   cd ~/rates-dashboard
   git remote add origin https://github.com/<you>/rates-dashboard.git
   git push -u origin main
   ```
4. In the repo, open **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `BANXICO_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. **Settings → Pages → Source: GitHub Actions.**
6. **Actions → Update rates → Run workflow.** Type the current GBM Smart Cash rate and tick **Send the Telegram summary now** to test. The site goes live at `https://<you>.github.io/rates-dashboard/`.

## Updating the GBM Smart Cash rate

When GBM changes its rate (usually right after a Banxico decision): **Actions → Update rates → Run workflow** and type the new rate. The dashboard warns you when the stored rate is more than 3 weeks old, or when Banxico has moved since you last set it.

## How the odds are estimated

If a central bank moves 25 bp at a meeting *k* days away, the average overnight rate over a bill's life of *h* days moves by 25 bp × (*h*−*k*)/*h*. So:

```
implied move at meeting = (bill yield − usual spread − policy rate) ÷ ((h − k) / h)
probability of a 25 bp move = implied move ÷ 25 bp   (capped at 100%)
```

- **Fed:** 1M / 3M / 6M T-bills against the effective fed funds rate.
- **Banxico:** CETES 28 / 91 / 182 days against the target rate.
- The script uses the shortest bill whose life covers at least a third of the time after the meeting.
- **Usual spread (basis):** the median of (bill − policy rate) over the last 2 years, counting only days when the rate didn't change during the bill's life. To override it, put a number in `config.json → probability`.

This is a rough estimate. Bill yields also move with supply, tax and liquidity, and when a meeting falls late in a bill's life small yield moves are magnified. CME's FedWatch uses fed funds futures and is more precise, but CME doesn't allow automated access. Use it to cross-check.

## Run locally

```sh
pip3 install -r requirements.txt
BANXICO_TOKEN=<token> python3 scripts/fetch_rates.py
python3 scripts/notify.py --dry-run          # print the Telegram message
python3 -m http.server -d site 8000          # then open http://localhost:8000
```

## Notes

- Banxico publishes next year's calendar around December; the script picks it up automatically. If parsing ever fails, add the dates to `config.json → banxico_calendar_manual`.
- The page shows a warning if a Banxico series title doesn't match what's expected. That means the series ID in `config.json → banxico_series` needs fixing.
- GitHub turns off scheduled workflows in repos with no activity for 60 days. The daily data commits normally count as activity.
