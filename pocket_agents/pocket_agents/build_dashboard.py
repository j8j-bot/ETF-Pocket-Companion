"""
Build the Pocket Signals opportunity dashboard from pipeline output.

Reads pocket_data/{ALL.csv,digest.json,signals.json}, computes a per-ETF
opportunity score (mean-reversion blend of RSI, distance from the 50-day
moving average, and 52-week range position), and injects the enriched data
into dashboard.template.html to produce a self-contained dashboard.html.

Run standalone:      python build_dashboard.py
Or automatically:    pipeline.py calls build() at the end of a healthy run.

Note: dashboard.html embeds a snapshot of the latest run. A *published*
artifact cannot fetch live data on load (sandboxed page, no network to
Yahoo or the repo), so "always current when opened" is achieved by
re-running the pipeline and re-publishing, not by client-side fetching.
"""

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "pocket_data"
TEMPLATE = HERE / "dashboard.template.html"
OUTPUT = HERE / "dashboard.html"

# ETF display names (CommSec Pocket lineup)
NAMES = {
    "IOZ": "Aussie Top 200", "SYI": "Aussie Dividends", "IOO": "Global 100",
    "IEM": "Emerging Markets", "NDQ": "US Tech 100 (NASDAQ)", "IXJ": "Global Healthcare",
    "ETHI": "Ethical Global", "DHHF": "All-in-One Growth", "GRNV": "Sustainable Aussie",
    "CRED": "Corporate Bonds",
}


def clamp(x, lo=-100, hi=100):
    return max(lo, min(hi, x))


def enrich():
    rows = defaultdict(list)  # ticker -> [(date, close, vol)]
    with open(DATA / "ALL.csv") as f:
        for r in csv.DictReader(f):
            rows[r["Ticker"]].append((r["Date"], float(r["Close"]), float(r["Volume"])))

    digest = json.loads((DATA / "digest.json").read_text())
    stats = digest["stats"]
    sig_doc = json.loads((DATA / "signals.json").read_text())
    sig_by_ticker = defaultdict(list)
    for s in sig_doc["signals"]:
        sig_by_ticker[s["code"]].append(s)

    out = []
    for code, series in rows.items():
        series.sort(key=lambda x: x[0])
        closes = [c for _, c, _ in series]
        st = stats[code]
        close, rsi, chg, volx = st["close"], st["rsi"], st["chg"], st["vol_ratio"]

        ma50 = statistics.mean(closes[-50:]) if len(closes) >= 50 else statistics.mean(closes)
        dist_ma = (close - ma50) / ma50 * 100
        hi52, lo52 = max(closes), min(closes)
        rng_pos = (close - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50
        ret20 = (close / closes[-21] - 1) * 100 if len(closes) > 21 else 0.0

        # Opportunity score, oriented to accumulation (buy low / oversold)
        c_rsi = clamp((50 - rsi) * 4)      # 45% weight
        c_ma = clamp(-dist_ma * 10)        # 30% weight
        c_rng = clamp((50 - rng_pos) * 2)  # 25% weight
        score = round(0.45 * c_rsi + 0.30 * c_ma + 0.25 * c_rng)

        if score >= 40:
            verdict, tone = "Strong Buy", "buy2"
        elif score >= 15:
            verdict, tone = "Accumulate", "buy1"
        elif score > -15:
            verdict, tone = "Hold", "hold"
        elif score > -40:
            verdict, tone = "Trim", "sell1"
        else:
            verdict, tone = "Reduce", "sell2"

        tail = closes[-90:]
        step = max(1, len(tail) // 60)
        spark = tail[::step]

        reasons = []
        if rsi <= 30:
            reasons.append(f"RSI {rsi} — oversold")
        elif rsi >= 70:
            reasons.append(f"RSI {rsi} — overbought")
        else:
            reasons.append(f"RSI {rsi}")
        reasons.append(("below" if dist_ma < 0 else "above") + f" 50-day avg by {abs(dist_ma):.1f}%")
        reasons.append(f"{rng_pos:.0f}% up its 52-week range")

        out.append({
            "code": code, "name": NAMES.get(code, code),
            "close": close, "chg": chg, "rsi": rsi, "volx": volx,
            "ma50": round(ma50, 2), "distMa": round(dist_ma, 1),
            "hi52": round(hi52, 2), "lo52": round(lo52, 2), "rngPos": round(rng_pos),
            "ret20": round(ret20, 1),
            "score": score, "verdict": verdict, "tone": tone,
            "cRsi": round(c_rsi), "cMa": round(c_ma), "cRng": round(c_rng),
            "reasons": reasons,
            "signals": [{"kind": s["kind"], "text": s["text"]} for s in sig_by_ticker.get(code, [])],
            "spark": [round(x, 2) for x in spark],
            "sparkFull": [round(x, 2) for x in closes[-250:]],
        })

    out.sort(key=lambda x: -x["score"])
    any_stat = next(iter(stats.values()))
    return {
        "generated": sig_doc.get("generated", ""),
        "lastDate": any_stat["last_date"],
        "verdict": sig_doc.get("verdict", ""),
        "tickers": out,
    }


def build():
    meta = enrich()
    payload = json.dumps(meta, separators=(",", ":"))
    tpl = TEMPLATE.read_text()
    if "/*__DATA__*/" not in tpl:
        raise SystemExit("template is missing the /*__DATA__*/ injection point")
    OUTPUT.write_text(tpl.replace("/*__DATA__*/", payload))
    return meta


if __name__ == "__main__":
    m = build()
    print(f"Built {OUTPUT.name}: {len(m['tickers'])} ETFs, prices as of {m['lastDate']}")
    for t in m["tickers"]:
        print(f"  {t['code']:5} {t['score']:+4d}  {t['verdict']}")
