"""Diagnose false positives in extractor precision evaluation."""
import json
from pathlib import Path

PATH = Path("backend/tests/fixtures/extractor/labeled_mentions.jsonl")
HIGH_CONF = 0.70

entries = [json.loads(l) for l in PATH.read_text(encoding="utf-8").splitlines() if l.strip()]

fp_on_labeled = []
fp_on_nosignal = []

for e in entries:
    if e.get("captured_output") is None:
        continue
    human = {lbl["ticker"].upper(): lbl["sentiment"] for lbl in (e.get("human_labels") or [])}
    for pred in (e.get("captured_output") or []):
        if float(pred.get("confidence", 0)) < HIGH_CONF:
            continue
        ticker = pred["ticker"].upper()
        sentiment = pred["sentiment"]
        if ticker in human and human[ticker] == sentiment:
            continue  # TP
        rec = {
            "id": e["id"],
            "body": e.get("body", "")[:120],
            "predicted": (ticker, sentiment, round(float(pred.get("confidence", 0)), 2)),
            "rationale": pred.get("rationale", ""),
            "human_labels": e.get("human_labels", []),
            "subreddit": e.get("subreddit", ""),
        }
        if human:
            fp_on_labeled.append(rec)
        else:
            fp_on_nosignal.append(rec)

print(f"FP on labeled entries (wrong ticker/sentiment): {len(fp_on_labeled)}")
print(f"FP on no-signal entries (hallucinations):       {len(fp_on_nosignal)}")
print()

print("--- FPs on no-signal entries (first 30) ---")
for r in fp_on_nosignal[:30]:
    sub = r["subreddit"]
    eid = r["id"]
    pred = r["predicted"]
    print(f"  [{sub}] {eid} | pred={pred[0]} {pred[1]} @{pred[2]}")
    print(f"    body: {r['body']!r}")
    print(f"    rationale: {r['rationale']!r}")
    print()

print("--- FPs on labeled entries (all) ---")
for r in fp_on_labeled:
    sub = r["subreddit"]
    eid = r["id"]
    pred = r["predicted"]
    hl = r["human_labels"]
    print(f"  [{sub}] {eid} | pred={pred[0]} {pred[1]} @{pred[2]} | human={hl}")
    print(f"    body: {r['body']!r}")
    print(f"    rationale: {r['rationale']!r}")
    print()
