"""
Pocket ETF pipeline: fetch -> compute -> Haiku QA -> outputs.

Design principle (anti-hallucination by construction):
  * ALL numbers are computed in Python. The model never calculates,
    never fetches, never estimates a figure.
  * Haiku only (a) classifies anomalies, (b) writes one-line descriptions
    for signals THAT CODE ALREADY DETECTED, (c) decides escalation.
  * Every model output is validated: strict JSON, checksum echo,
    ticker whitelist, signal-id whitelist, and a numeral check that
    rejects any number not present in the input digest.
  * On any validation failure the pipeline falls back to deterministic
    template text. The LLM can improve the output; it can never corrupt it.

Setup:  pip install yfinance pandas requests
        export ANTHROPIC_API_KEY=sk-ant-...   (optional; omit to skip Haiku)
Run:    python pipeline.py
Out:    pocket_data/ALL.csv, pocket_data/signals.json, pocket_data/qa_log.txt
        pocket_data/digest.json  (numbers whitelist, reused by report skill)
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

CODES = ["IOZ", "SYI", "IOO", "IEM", "NDQ", "IXJ", "ETHI", "DHHF", "GRNV", "CRED"]
OUT = Path("pocket_data")
OUT.mkdir(exist_ok=True)

# Signal thresholds — the single source of truth (mirrored in pocket-signals skill)
TH = {"move_pct": 2.0, "vol_ratio": 2.0, "rsi_hi": 70, "rsi_lo": 30, "ma_window": 50}

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_QA_OUTPUT_TOKENS = 500          # hard cap: QA never needs more
API_URL = "https://api.anthropic.com/v1/messages"


# ----------------------------------------------------------------------
# Deterministic layer: fetch + indicators (no LLM anywhere here)
# ----------------------------------------------------------------------

def fetch_all():
    data = {}
    for code in CODES:
        df = yf.download(f"{code}.AX", period="1y", interval="1d",
                         auto_adjust=False, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()[["Date", "Close", "Volume"]].dropna()
        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        data[code] = df
    return data


def rsi14(closes):
    if len(closes) < 15:
        return None
    p = closes[-15:]
    g = sum(max(p[i] - p[i - 1], 0) for i in range(1, 15))
    l = sum(max(p[i - 1] - p[i], 0) for i in range(1, 15))
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def compute(data):
    """Per-ticker stats + fired signals. Every number the model may
    later mention is minted HERE and recorded in the whitelist."""
    stats, signals, anomalies = {}, [], []
    for code in CODES:
        df = data.get(code)
        if df is None or df.empty:
            anomalies.append({"code": code, "issue": "no_data"})
            continue
        closes = df["Close"].tolist()
        vols = df["Volume"].tolist()
        last, prev = closes[-1], closes[-2] if len(closes) > 1 else closes[-1]
        chg = (last / prev - 1) * 100 if prev else 0.0
        vol_ratio = None
        if len(vols) >= 21 and sum(vols[-21:-1]) > 0:
            vol_ratio = vols[-1] / (sum(vols[-21:-1]) / 20)
        rsi = rsi14(closes)
        ma = sum(closes[-TH["ma_window"]:]) / TH["ma_window"] if len(closes) >= TH["ma_window"] else None
        ma_prev = (sum(closes[-TH["ma_window"] - 1:-1]) / TH["ma_window"]
                   if len(closes) >= TH["ma_window"] + 1 else None)

        s = {"close": round(last, 2), "chg": round(chg, 2),
             "vol_ratio": round(vol_ratio, 1) if vol_ratio else None,
             "rsi": round(rsi) if rsi is not None else None,
             "last_date": df["Date"].iloc[-1], "rows": len(df)}
        stats[code] = s

        # deterministic anomaly checks
        if last <= 0:
            anomalies.append({"code": code, "issue": "nonpositive_price"})
        if abs(chg) > 15:
            anomalies.append({"code": code, "issue": f"jump_{s['chg']}pct"})
        last_dt = datetime.strptime(s["last_date"], "%Y-%m-%d")
        if datetime.now() - last_dt > timedelta(days=5):
            anomalies.append({"code": code, "issue": "stale_data"})

        # deterministic signal detection (model may only DESCRIBE these)
        sid = 0
        def add(kind, value):
            nonlocal sid
            sid += 1
            signals.append({"id": f"{code}-{sid}", "code": code,
                            "kind": kind, "value": value})
        if abs(chg) >= TH["move_pct"]:
            add("price_move", s["chg"])
        if vol_ratio and vol_ratio >= TH["vol_ratio"]:
            add("volume_spike", s["vol_ratio"])
        if rsi is not None and (rsi >= TH["rsi_hi"] or rsi <= TH["rsi_lo"]):
            add("rsi_extreme", s["rsi"])
        if ma and ma_prev:
            if prev <= ma_prev < ma < last or prev <= ma_prev and last > ma:
                if prev <= ma_prev and last > ma:
                    add("ma_cross_up", round(ma, 2))
            if prev >= ma_prev and last < ma:
                add("ma_cross_down", round(ma, 2))
    return stats, signals, anomalies


def build_digest(stats, signals, anomalies):
    """Compact text digest (~600 tokens for 10 tickers) + numeral whitelist."""
    lines = [f"{c} close={s['close']} chg={s['chg']}% volx={s['vol_ratio']} "
             f"rsi={s['rsi']} date={s['last_date']} rows={s['rows']}"
             for c, s in stats.items()]
    digest = "\n".join(lines)
    allowed_numbers = set()
    for s in stats.values():
        for v in (s["close"], s["chg"], abs(s["chg"]), s["vol_ratio"], s["rsi"], s["rows"]):
            if v is not None:
                allowed_numbers.add(f"{v:g}")
    for sig in signals:
        allowed_numbers.add(f"{sig['value']:g}")
        allowed_numbers.add(f"{abs(sig['value']):g}")
    for n in (TH["move_pct"], TH["vol_ratio"], TH["rsi_hi"], TH["rsi_lo"],
              TH["ma_window"], 14, 20):
        allowed_numbers.add(f"{n:g}")
    checksum = hashlib.md5(digest.encode()).hexdigest()[:8]
    return digest, allowed_numbers, checksum


# ----------------------------------------------------------------------
# Haiku QA layer — with hard guards on everything it returns
# ----------------------------------------------------------------------

def template_note(sig):
    t = {"price_move": f"moved {sig['value']:+g}% in the latest session",
         "volume_spike": f"volume ran {sig['value']:g}x its 20-day average",
         "rsi_extreme": f"14-day RSI at {sig['value']:g}",
         "ma_cross_up": f"closed above its {TH['ma_window']}-day average ({sig['value']:g})",
         "ma_cross_down": f"closed below its {TH['ma_window']}-day average ({sig['value']:g})"}
    return t.get(sig["kind"], sig["kind"])


NUM_RE = re.compile(r"\d+(?:\.\d+)?")

def numerals_ok(text, allowed):
    return all(f"{float(n):g}" in allowed for n in NUM_RE.findall(text))


def haiku_qa(digest, signals, anomalies, allowed, checksum, log):
    key = os.environ.get("ANTHROPIC_API_KEY")
    fallback = {
        "verdict": "warn" if anomalies else "ok",
        "notes": {s["id"]: template_note(s) for s in signals},
        "escalate": bool(anomalies) or any(
            len([x for x in signals if x["code"] == s["code"]]) >= 2 for s in signals),
        "source": "template",
    }
    if not key:
        log.append("No ANTHROPIC_API_KEY - using template descriptions.")
        return fallback

    sig_lines = "\n".join(f"{s['id']} {s['code']} {s['kind']} value={s['value']:g}"
                          for s in signals) or "(none)"
    anom_lines = "\n".join(f"{a['code']}: {a['issue']}" for a in anomalies) or "(none)"
    prompt = (
        "You are a data QA step in an ETF pipeline. Use ONLY the data below; "
        "do not add numbers, tickers, or facts not present in it.\n\n"
        f"DIGEST:\n{digest}\n\nDETECTED SIGNALS:\n{sig_lines}\n\n"
        f"ANOMALIES:\n{anom_lines}\n\nCHECKSUM: {checksum}\n\n"
        "Reply with ONLY this JSON (no fences):\n"
        '{"checksum":"<copy CHECKSUM exactly>","verdict":"ok|warn|fail",'
        '"anomaly_reads":[{"code":"...","likely":"data_error|corporate_action|holiday|real_move"}],'
        '"notes":[{"id":"<signal id from list>","text":"<one plain sentence, numbers only from digest>"}],'
        '"escalate":true|false}'
    )

    for attempt in (1, 2):
        try:
            r = requests.post(API_URL, timeout=60, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"},
                json={"model": HAIKU_MODEL, "max_tokens": MAX_QA_OUTPUT_TOKENS,
                      "temperature": 0,
                      "messages": [{"role": "user", "content": prompt}]})
            body = r.json()
            text = "".join(b.get("text", "") for b in body.get("content", []))
            m = re.search(r"\{[\s\S]*\}", text)
            out = json.loads(m.group(0))

            # GUARD 1: checksum echo proves the model processed THIS digest
            if out.get("checksum") != checksum:
                raise ValueError("checksum mismatch")
            # GUARD 2: verdict must be in enum
            if out.get("verdict") not in ("ok", "warn", "fail"):
                raise ValueError("bad verdict")
            # GUARD 3: notes may only reference signal ids code detected;
            # numerals in text must exist in the whitelist. Violations are
            # replaced per-note with the deterministic template.
            valid_ids = {s["id"]: s for s in signals}
            notes = {}
            for n in out.get("notes", []):
                sid = n.get("id")
                if sid in valid_ids and isinstance(n.get("text"), str) \
                        and numerals_ok(n["text"], allowed) and len(n["text"]) < 200:
                    notes[sid] = n["text"]
                elif sid in valid_ids:
                    notes[sid] = template_note(valid_ids[sid])
                    log.append(f"Note for {sid} failed numeral guard - templated.")
            for sid, s in valid_ids.items():
                notes.setdefault(sid, template_note(s))
            # GUARD 4: anomaly reads restricted to known codes + enum
            reads = [a for a in out.get("anomaly_reads", [])
                     if a.get("code") in CODES and a.get("likely") in
                     ("data_error", "corporate_action", "holiday", "real_move")]
            log.append(f"Haiku QA ok (attempt {attempt}).")
            return {"verdict": out["verdict"], "notes": notes,
                    "anomaly_reads": reads,
                    "escalate": bool(out.get("escalate")) or bool(anomalies),
                    "source": "haiku"}
        except Exception as e:  # noqa: BLE001
            log.append(f"Haiku QA attempt {attempt} failed: {e}")
    log.append("Falling back to template descriptions.")
    return fallback


# ----------------------------------------------------------------------

def main():
    log = [f"Run {datetime.now().isoformat(timespec='seconds')}"]
    data = fetch_all()
    ok_codes = [c for c in CODES if c in data and not data[c].empty]
    if not ok_codes:
        log.append("FATAL: no data fetched. Keeping previous outputs untouched.")
        (OUT / "qa_log.txt").write_text("\n".join(log))
        sys.exit(1)

    combined = []
    for c in ok_codes:
        d = data[c].copy()
        d.insert(0, "Ticker", c)
        combined.append(d)
    pd.concat(combined).to_csv(OUT / "ALL.csv", index=False)

    stats, signals, anomalies = compute(data)
    digest, allowed, checksum = build_digest(stats, signals, anomalies)
    qa = haiku_qa(digest, signals, anomalies, allowed, checksum, log)

    (OUT / "digest.json").write_text(json.dumps(
        {"digest": digest, "allowed_numbers": sorted(allowed),
         "checksum": checksum, "stats": stats}, indent=2))
    (OUT / "signals.json").write_text(json.dumps(
        {"generated": datetime.now().isoformat(timespec="seconds"),
         "verdict": qa["verdict"], "escalate": qa["escalate"],
         "signals": [{**s, "text": qa["notes"][s["id"]]} for s in signals],
         "anomalies": anomalies,
         "anomaly_reads": qa.get("anomaly_reads", []),
         "qa_source": qa["source"]}, indent=2))
    log.append(f"{len(ok_codes)}/10 tickers, {len(signals)} signals, "
               f"{len(anomalies)} anomalies, verdict={qa['verdict']}.")
    (OUT / "qa_log.txt").write_text("\n".join(log))
    print("\n".join(log))


if __name__ == "__main__":
    main()
