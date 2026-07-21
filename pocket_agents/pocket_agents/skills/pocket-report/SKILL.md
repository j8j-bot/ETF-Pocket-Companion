---
name: pocket-report
description: Generate the bi-weekly fundamental report and impact matrix for the Pocket ETF dashboard using Sonnet with web search, then verify it deterministically. Use when the user asks for the fortnightly report, the impact matrix, or "what's moving my ETFs".
---

# Pocket ETF bi-weekly report (Sonnet)

## Inputs
1. `pocket_data/digest.json` from the latest pipeline run — this is the ONLY
   permitted source of market numbers. If it is older than 3 trading days,
   run the pocket-fetch skill first.
2. Web search, limited to these angles (3-6 searches total, no more):
   - latest RBA monetary policy decision / statement
   - AUD/USD direction over the past fortnight
   - major news for the ETF themes (US mega-cap tech earnings, healthcare,
     emerging markets / China, Australian banks & dividends, credit markets)

## Output — write `pocket_data/report.json`:
```json
{
  "date": "Bi-weekly report - <DD Mon YYYY>",
  "headline": "<one line, factual>",
  "sections": [
    {"title": "...", "text": "...", "source_url": "https://..."}
  ],
  "impact_matrix": {
    "IOZ": {"<event>": "tailwind|headwind|neutral|high_sensitivity"},
    "... all 10 codes ..."
  }
}
```
2-4 sections. 3-5 events in the matrix, same events for every code.

## Anti-hallucination contract (token-cheap by design)
- **Closed-number rule:** the report text may contain ONLY numerals that
  appear in `digest.json -> allowed_numbers`, plus years and dates. Do not
  quote index levels, rate values, or percentages from search results —
  describe them directionally ("held steady", "drifted lower", "rallied").
  This one rule removes the main hallucination surface at zero token cost.
- **Source per section:** each section carries the URL of the page that
  supports it. No URL = section doesn't ship.
- **Directional chips only:** the matrix is an enum, never free text.
- **No advice language:** describe, never recommend. The verifier blocks
  buy/sell/should-invest phrasing.

## Verification loop (mandatory)
1. `python verify_report.py pocket_data/report.json pocket_data/digest.json`
2. If REJECTED: fix ONLY the listed violations and re-verify. Maximum two
   regeneration attempts; if it still fails, show the user the violations
   instead of shipping the report. Never edit verify_report.py to make a
   report pass.

## Token budget
Target ~15-25k input (searches + digest) / <=2k output per fortnight.
Do not paste raw CSVs or full articles into the prompt — the digest and
search snippets are sufficient.
