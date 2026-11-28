"""Brand extraction v2: an LLM reads each answer and returns brands + sentiment.

Flow per batch of answers:
  1. send N answers in ONE request (saves quota) with the same rules used for labeling
  2. the model returns JSON (forced by a schema)
  3. Python cleans it:
       - map names to canonical brands.yaml names (unknown names kept + reported)
       - drop any brand that does not actually appear in the text (hallucination check)
       - positions recomputed from where each brand first appears in the text
  4. save to mentions with extractor = 'v2_llm', and mark the answer as done

Only answers not yet processed by v2 are sent, so each answer costs quota once.

Usage:  python extract_llm.py             # process new answers
        python extract_llm.py --dry-run   # call the model, print, don't save
        python extract_llm.py --limit 5   # at most 5 answers this run
"""
import argparse
import os
import time

import psycopg
import yaml
from dotenv import load_dotenv
from google.genai import types
from pydantic import BaseModel

from extract import compile_patterns, extract_v1, normalize
from providers import _gemini
from run import is_daily_quota_error

EXTRACTOR = "v2_llm"

PROMPT = """You extract coffee and cafe brands from AI-generated answers.

Rules:
- Include coffee chains and cafe chains, including dessert cafes (cake, donut, bingsu cafes)
  and unmanned/self-service cafe brands.
- Exclude convenience stores (GS25, CU, 7-Eleven), study cafes, map apps, and generic terms.
- Each brand once per answer. Use the brand's common English name.
- sentiment:
    positive = recommended or praised, including a brand offered as an alternative
               inside a recommendation (e.g. "Best budget chain: X (or Y)" -> Y is positive)
    neutral  = only listed or mentioned in passing, or compared without judgement
    negative = criticized or advised against
- Only include brands that are actually written in the text.

Return one result per answer, using the answer_id given.

{answers}"""


class Brand(BaseModel):
    brand: str
    sentiment: str


class AnswerResult(BaseModel):
    answer_id: int
    brands: list[Brand]


def call_llm(model: str, answers):
    """answers: [(answer_id, text)] -> {answer_id: [Brand]}"""
    block = "\n\n".join(f"### answer_id={aid}\n{text}" for aid, text in answers)
    resp = _gemini().models.generate_content(
        model=model,
        contents=PROMPT.format(answers=block),
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=list[AnswerResult],
        ),
    )
    results = resp.parsed or []
    return {r.answer_id: r.brands for r in results}


def first_index(text_norm: str, brand: str, aliases_by_brand: dict, patterns) -> int:
    """Where a brand first appears in the (normalized) text, or -1."""
    best = -1
    for b, _alias, rx in patterns:
        if b != brand:
            continue
        m = rx.search(text_norm)
        if m and (best == -1 or m.start() < best):
            best = m.start()
    if best == -1 and brand not in aliases_by_brand:   # unknown brand: plain search
        best = text_norm.find(normalize(brand))
    return best


def clean(text: str, raw_brands, brands: dict, patterns):
    """Map to canonical names, drop hallucinations, recompute positions."""
    t = normalize(text)
    kept, unknown, dropped = {}, [], []
    for rb in raw_brands:
        name = rb.brand.strip()
        canon = next((b for b in brands if normalize(b) == normalize(name)), None)
        if canon is None:
            hit = extract_v1(name, patterns)
            canon = hit[0]["brand"] if hit else name
        idx = first_index(t, canon, brands, patterns)
        if idx == -1:
            dropped.append(name)          # not in the text -> hallucination
            continue
        if canon not in brands:
            unknown.append(canon)
        sentiment = rb.sentiment.lower() if rb.sentiment.lower() in ("positive", "neutral", "negative") else None
        if canon not in kept or idx < kept[canon][0]:
            kept[canon] = (idx, sentiment)
    ordered = sorted(kept.items(), key=lambda kv: kv[1][0])
    rows = [{"brand": b, "position": i + 1, "sentiment": s} for i, (b, (_, s)) in enumerate(ordered)]
    return rows, unknown, dropped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    load_dotenv()
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)["extraction"]
    with open("brands.yaml", encoding="utf-8") as f:
        brands = yaml.safe_load(f)
    patterns = compile_patterns(brands)
    model, batch_size, pause = cfg["model"], cfg.get("batch_size", 5), cfg.get("sleep_between_calls", 5)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:  # commit each save immediately
        todo = conn.execute(
            """SELECT r.id, r.answer_text FROM raw_answers r
               LEFT JOIN extracted_answers e ON e.answer_id = r.id AND e.extractor = %s
               WHERE r.error IS NULL AND r.answer_text IS NOT NULL AND e.answer_id IS NULL
               ORDER BY r.id LIMIT %s""",
            (EXTRACTOR, args.limit),
        ).fetchall()
        print(f"{len(todo)} answers to extract with {model} (batches of {batch_size})\n")
        texts = dict(todo)
        all_unknown, done = set(), 0

        for i in range(0, len(todo), batch_size):
            batch = todo[i:i + batch_size]
            try:
                results = call_llm(model, batch)
            except Exception as e:  # noqa: BLE001
                if is_daily_quota_error(e):
                    print(f"[STOP] {model}: daily quota used up. Re-run after it resets.")
                else:
                    print(f"[FAIL] batch {[a for a, _ in batch]}: {type(e).__name__}: {str(e)[:200]}")
                break

            for answer_id, _ in batch:
                if answer_id not in results:
                    print(f"[skip] answer #{answer_id}: missing from model output, will retry next run")
                    continue
                rows, unknown, dropped = clean(texts[answer_id], results[answer_id], brands, patterns)
                all_unknown.update(unknown)
                summary = ", ".join(f"{r['position']}.{r['brand']}({(r['sentiment'] or '?')[:3]})" for r in rows)
                print(f"[ok] #{answer_id}: {summary or '(no brands)'}"
                      + (f"   dropped (not in text): {dropped}" if dropped else ""))
                if args.dry_run:
                    continue
                with conn.transaction():
                    conn.execute("DELETE FROM mentions WHERE answer_id=%s AND extractor=%s", (answer_id, EXTRACTOR))
                    for r in rows:
                        conn.execute(
                            "INSERT INTO mentions (answer_id, brand, position, sentiment, extractor) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (answer_id, r["brand"], r["position"], r["sentiment"], EXTRACTOR),
                        )
                    conn.execute(
                        "INSERT INTO extracted_answers (answer_id, extractor, model) VALUES (%s,%s,%s) "
                        "ON CONFLICT (answer_id, extractor) DO UPDATE SET model=EXCLUDED.model, extracted_at=now()",
                        (answer_id, EXTRACTOR, model),
                    )
                done += 1
            time.sleep(pause)

    print(f"\n{done} answers saved as '{EXTRACTOR}'" if not args.dry_run else "\n(dry run, nothing saved)")
    if all_unknown:
        print("Brands the model found that are NOT in brands.yaml (check + add real ones):")
        for b in sorted(all_unknown):
            print(f"  - {b}")


if __name__ == "__main__":
    main()
