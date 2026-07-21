---
name: pocket-fetch
description: Run or schedule the daily CommSec Pocket ETF data pipeline (fetch prices, compute indicators, Haiku QA). Use when the user asks to fetch/refresh ETF data, set up or fix the daily schedule, or investigate a failed run.
---

# Pocket ETF daily fetch

## What this does
`pipeline.py` fetches 1y of daily OHLCV for the 10 Pocket ETFs
(IOZ, SYI, IOO, IEM, NDQ, IXJ, ETHI, DHHF, GRNV, CRED — Yahoo suffix `.AX`),
computes all indicators in Python, runs a Haiku QA pass, and writes:

- `pocket_data/ALL.csv` — import file for the dashboard
- `pocket_data/signals.json` — fired signals with validated descriptions
- `pocket_data/digest.json` — per-ticker stats + `allowed_numbers` whitelist
- `pocket_data/qa_log.txt` — what happened this run

## Procedure
1. Ensure deps: `pip install pandas requests`. (No `yfinance` — prices come
   straight from Yahoo's public chart endpoint via plain `requests`, since
   yfinance's curl_cffi TLS-fingerprint impersonation can't complete a
   handshake through a MITM-style egress proxy such as a Claude Code cloud
   environment's.)
2. `ANTHROPIC_API_KEY` env var enables the Haiku QA layer. Without it the
   pipeline still runs and uses deterministic template text — that is fine,
   never block on a missing key.
3. Run: `python pipeline.py`. A healthy run prints `10/10 tickers ... verdict=ok`.
4. Scheduling: weekdays 17:30 local (after ASX close + settlement of feeds).
   - macOS/Linux: cron entry `30 17 * * 1-5 cd <project> && python pipeline.py >> cron.log 2>&1`
   - Windows: Task Scheduler, same time, action `python pipeline.py`,
     "Start in" set to the project folder.
   Always use the absolute python path (`which python` / `where python`).

## Non-negotiable rules
- **Never fabricate or hand-edit price data.** If the fetch fails, the
  pipeline exits and leaves the previous outputs untouched — that is the
  correct behaviour. Fix the fetch; do not fill gaps by hand or from memory.
- **Never bypass the guards in `haiku_qa()`** (checksum echo, signal-id
  whitelist, numeral whitelist). If Haiku output keeps failing guards,
  the template fallback is the answer, not weaker guards.
- Fewer than 10/10 tickers or `verdict=fail` → read `qa_log.txt`, diagnose,
  and tell the user plainly what's wrong. Common causes: yfinance breakage
  (upgrade it), a delisted/renamed ticker (confirm on the ASX before editing
  CODES), network issues.

## Token budget
One Haiku call per run: ~700 input / ≤500 output tokens (capped in code).
Do not add more model calls to this path; it is designed to stay under
a cent per run.
