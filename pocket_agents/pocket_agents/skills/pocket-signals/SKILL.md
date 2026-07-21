---
name: pocket-signals
description: Interpret, tune, or extend the technical signal layer of the Pocket ETF pipeline (thresholds, escalation, notifications). Use when the user asks why a signal fired or didn't, wants thresholds changed, or wants alerts delivered somewhere.
---

# Pocket ETF signals & escalation

## Thresholds (single source of truth: `TH` dict in pipeline.py)
| Signal | Fires when | Severity |
|---|---|---|
| price_move | abs 1-day change >= 2.0% | high |
| volume_spike | volume >= 2.0x its 20-day average | high |
| rsi_extreme | RSI(14) >= 70 or <= 30 | med |
| ma_cross_up / ma_cross_down | close crosses the 50-day MA | low / med |

To change a threshold: edit `TH` in pipeline.py AND the table above so the
skill stays truthful. Never change one without the other.

## Escalation rule
`escalate: true` when any anomaly exists, or when one ticker fires 2+
signals in the same session. Escalation means: surface it to the user
(and, in Phase B, trigger the Sonnet digest + push notification).

## Anti-hallucination contract (do not weaken)
- Signals are **detected by code only**. Haiku's job is limited to writing a
  one-sentence description per detected signal id. It cannot add, remove,
  or reclassify signals.
- Every numeral in a description must exist in `digest.json ->
  allowed_numbers`. The pipeline enforces this and swaps violating text for
  a template; treat a swap in `qa_log.txt` as a warning sign, not a bug to
  route around.
- When summarising signals for the user, quote from `signals.json` — do not
  re-derive or re-estimate values from memory.

## Notification format (for future delivery integrations)
One line per signal: `ETF — kind: text` ordered high > med > low severity,
prefixed with the verdict if not ok. Keep it under 500 characters total so
it fits SMS/push payloads.
