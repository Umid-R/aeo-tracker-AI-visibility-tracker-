"""Export dashboard data: mention rate per AI model and brand, start period vs latest period.

mention rate = answers that mention the brand / all answers   (in that period, for that model)

To compare periods fairly, only questions answered in BOTH periods are counted.
(e.g. if day 1 only got q01-q07 because of quota, both periods use q01-q07.)

Periods: with N days of data, compare the first W days with the last W days,
where W = min(7, N // 2), at least 1. Two days of data -> day 1 vs day 2.

Brands come from extraction v1 (approved brands.yaml list, 100% precision on labels).

Usage:  python export_metrics.py                  # reads DB, writes dashboard/data/metrics.json
        python export_metrics.py --rows rows.json # build from an exported rows file instead
"""
import argparse
import datetime
import json
import os
from collections import defaultdict

EXTRACTOR = "v1_string"
OUT = os.path.join("dashboard", "data", "metrics.json")

ROWS_SQL = """
SELECT r.id, r.run_date::text, r.model, r.question_id,
       COALESCE(json_agg(m.brand ORDER BY m.position) FILTER (WHERE m.brand IS NOT NULL), '[]')
FROM raw_answers r
LEFT JOIN mentions m ON m.answer_id = r.id AND m.extractor = %s
WHERE r.error IS NULL AND r.answer_text IS NOT NULL
GROUP BY r.id ORDER BY r.id
"""


def model_label(model: str) -> str:
    """'gemini-3.6-flash' -> 'Gemini 3.6 Flash', 'gpt-5-mini' -> 'GPT 5 Mini'."""
    words = model.replace("_", "-").split("-")
    return " ".join(w.upper() if w.lower() in ("gpt", "ai") else w.capitalize() for w in words)


def compute(rows):
    """rows: [{'run_date', 'model', 'question_id', 'brands': [...]}] -> dashboard dict."""
    by_model = defaultdict(list)
    for r in rows:
        by_model[r["model"]].append(r)

    models = {}
    for model, answers in by_model.items():
        dates = sorted({a["run_date"] for a in answers})
        w = max(1, min(7, len(dates) // 2))
        if len(dates) < 2:
            start_dates = end_dates = dates          # only one day: no change yet
        else:
            start_dates, end_dates = dates[:w], dates[-w:]

        start = [a for a in answers if a["run_date"] in start_dates]
        end = [a for a in answers if a["run_date"] in end_dates]
        common = {a["question_id"] for a in start} & {a["question_id"] for a in end}
        start = [a for a in start if a["question_id"] in common]
        end = [a for a in end if a["question_id"] in common]

        def rates(group):
            counts = defaultdict(int)
            for a in group:
                for b in set(a["brands"]):
                    counts[b] += 1
            return {b: 100 * c / len(group) for b, c in counts.items()} if group else {}

        rs, re_ = rates(start), rates(end)
        brands = []
        for b in set(rs) | set(re_):
            s, e = rs.get(b, 0.0), re_.get(b, 0.0)
            brands.append({"brand": b, "start": round(s, 1), "end": round(e, 1),
                           "change": round(e - s, 1)})
        brands.sort(key=lambda x: (-x["end"], -x["start"], x["brand"]))

        models[model] = {
            "label": model_label(model),
            "start": {"from": start_dates[0], "to": start_dates[-1], "answers": len(start)},
            "end": {"from": end_dates[0], "to": end_dates[-1], "answers": len(end)},
            "questions_compared": len(common),
            "total_answers": len(answers),
            "days": len(dates),
            "brands": brands,
        }

    return {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "metric": "mention_rate",
        "extractor": EXTRACTOR,
        "category": "Coffee & cafe chains in Korea",
        "models": models,
    }


def fetch_rows():
    import psycopg
    from dotenv import load_dotenv
    load_dotenv()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        out = conn.execute(ROWS_SQL, (EXTRACTOR,)).fetchall()
    return [{"run_date": d, "model": m, "question_id": q, "brands": b} for _, d, m, q, b in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", help="JSON file with rows instead of reading the DB")
    args = ap.parse_args()
    rows = json.load(open(args.rows, encoding="utf-8")) if args.rows else fetch_rows()
    data = compute(rows)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    for m, d in data["models"].items():
        top = ", ".join(f"{b['brand']} {b['end']}%" for b in d["brands"][:5])
        print(f"{d['label']}: {d['start']['from']} vs {d['end']['to']}, "
              f"{d['questions_compared']} questions compared | top: {top}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
