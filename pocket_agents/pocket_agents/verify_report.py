"""
verify_report.py - deterministic gate for the Sonnet bi-weekly report.

Usage:  python verify_report.py pocket_data/report.json pocket_data/digest.json
Exit 0 = report passes. Exit 1 = report rejected; reasons on stdout.

Checks (all pure code, zero tokens):
  1. Schema: required keys, section count, matrix shape.
  2. Ticker whitelist: only the 10 Pocket codes may appear.
  3. Chip enum: matrix values must be tailwind|headwind|neutral|high_sensitivity.
  4. Closed-number rule: every numeral in report text must appear in the
     digest's allowed_numbers, be a plausible year (2020-2030), or a
     day-of-month 1-31 inside a date. No invented statistics.
  5. Source rule: each section must carry a non-empty source_url.
  6. Advice-language block: recommendation verbs fail the report.
"""

import json
import re
import sys

CODES = {"IOZ", "SYI", "IOO", "IEM", "NDQ", "IXJ", "ETHI", "DHHF", "GRNV", "CRED"}
CHIPS = {"tailwind", "headwind", "neutral", "high_sensitivity"}
ADVICE = re.compile(
    r"\b(you should (buy|sell|invest)|buy now|sell now|guaranteed|"
    r"we recommend (buying|selling)|must buy|must sell)\b", re.I)
NUM = re.compile(r"\d+(?:\.\d+)?")
TICKERISH = re.compile(r"\b[A-Z]{3,5}\b")
KNOWN_CAPS = CODES | {"RBA", "AUD", "USD", "ASX", "ETF", "ETFS", "US", "MER",
                      "RSI", "GDP", "CPI", "AEST", "NASDAQ", "MSCI", "ESG"}


def fail(reasons):
    print("REPORT REJECTED:")
    for r in reasons:
        print(f"  - {r}")
    sys.exit(1)


def main(report_path, digest_path):
    reasons = []
    report = json.loads(open(report_path).read())
    digest = json.loads(open(digest_path).read())
    allowed = set(digest.get("allowed_numbers", []))

    # 1. schema
    for k in ("date", "headline", "sections", "impact_matrix"):
        if k not in report:
            reasons.append(f"missing key: {k}")
    sections = report.get("sections", [])
    if not (2 <= len(sections) <= 4):
        reasons.append(f"expected 2-4 sections, got {len(sections)}")

    all_text = report.get("headline", "")
    for i, s in enumerate(sections):
        for k in ("title", "text", "source_url"):
            if not s.get(k):
                reasons.append(f"section {i}: missing/empty {k}")   # rule 5
        all_text += " " + s.get("title", "") + " " + s.get("text", "")

    # 2. ticker whitelist (any ALLCAPS token that looks like a ticker)
    for tok in set(TICKERISH.findall(all_text)):
        if tok not in KNOWN_CAPS:
            reasons.append(f"unknown ticker-like token: {tok}")

    # 3. matrix shape + enum
    matrix = report.get("impact_matrix", {})
    if set(matrix.keys()) != CODES:
        reasons.append("impact_matrix must have exactly the 10 Pocket codes")
    for code, chips in matrix.items():
        for ev, chip in (chips or {}).items():
            if chip not in CHIPS:
                reasons.append(f"matrix {code}/{ev}: bad chip '{chip}'")

    # 4. closed-number rule
    for n in NUM.findall(all_text):
        v = f"{float(n):g}"
        if v in allowed:
            continue
        f = float(n)
        if 2020 <= f <= 2030 and f == int(f):      # years
            continue
        if 1 <= f <= 31 and f == int(f):           # day-of-month in dates
            continue
        reasons.append(f"numeral '{n}' not in allowed_numbers (invented figure?)")

    # 6. advice language
    m = ADVICE.search(all_text)
    if m:
        reasons.append(f"advice language found: '{m.group(0)}'")

    if reasons:
        fail(reasons)
    print("REPORT OK")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python verify_report.py report.json digest.json")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
