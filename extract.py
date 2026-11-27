"""Brand extraction v1: plain string matching (no AI).

For every successful answer in raw_answers:
  1. clean the text (lowercase, strip markdown, unify apostrophes)
  2. find every alias from brands.yaml (longest aliases first, so overlapping
     names can't double-match the same piece of text)
  3. keep each brand once, at its FIRST appearance
  4. position = order of first appearance (1 = mentioned first)

v1 is free and deterministic, so each run simply rebuilds all v1 rows.
That way edits to brands.yaml always apply to the full history.

Usage:  python extract.py            # rebuild v1 mentions in the DB
        python extract.py --dry-run  # print results, don't write
"""
import argparse
import os
import re
import unicodedata
from collections import Counter

import psycopg
import yaml
from dotenv import load_dotenv

EXTRACTOR = "v1_string"


# ---------- text cleanup ----------

def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'")  # curly -> straight apostrophe
    text = re.sub(r"[*#`_>]", "", text)                       # markdown symbols
    return text.lower()


# ---------- patterns ----------

def compile_patterns(brands: dict):
    """Return [(brand, alias, regex)] sorted longest alias first."""
    pats = []
    for brand, aliases in brands.items():
        for alias in aliases:
            a = normalize(alias)
            if a.isascii():
                # whole word: no letter/digit right before or after the alias
                rx = re.compile(rf"(?<![\w]){re.escape(a)}(?![\w])")
            else:
                # Korean: plain substring (particles like 는/에서 attach directly)
                rx = re.compile(re.escape(a))
            pats.append((brand, alias, rx))
    pats.sort(key=lambda p: len(p[1]), reverse=True)
    return pats


# ---------- extraction ----------

def extract_v1(text: str, patterns):
    """Return [{'brand', 'position', 'alias'}] ordered by first appearance."""
    t = normalize(text)
    claimed = []          # (start, end) spans already used by a longer alias
    first_seen = {}       # brand -> (index, alias)

    for brand, alias, rx in patterns:
        for m in rx.finditer(t):
            s, e = m.span()
            if any(s < ce and cs < e for cs, ce in claimed):
                continue  # this text is already part of a longer match
            claimed.append((s, e))
            if brand not in first_seen or s < first_seen[brand][0]:
                first_seen[brand] = (s, alias)

    ordered = sorted(first_seen.items(), key=lambda kv: kv[1][0])
    return [{"brand": b, "position": i + 1, "alias": a}
            for i, (b, (_, a)) in enumerate(ordered)]


def unknown_bold_terms(text: str, patterns):
    """Bold **terms** that match no known alias -> candidates for brands.yaml."""
    out = []
    for raw in re.findall(r"\*\*(.+?)\*\*", text):
        term = raw.strip().rstrip(":").strip()
        if not term or len(term.split()) > 4 or any(ch.isdigit() for ch in term) or ":" in term:
            continue
        if extract_v1(term, patterns):
            continue
        out.append(term)
    return out


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print results, don't write to DB")
    args = ap.parse_args()

    load_dotenv()
    with open("brands.yaml", encoding="utf-8") as f:
        brands = yaml.safe_load(f)
    patterns = compile_patterns(brands)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        answers = conn.execute(
            "SELECT id, run_date, model, question_id, answer_text FROM raw_answers "
            "WHERE error IS NULL AND answer_text IS NOT NULL ORDER BY id"
        ).fetchall()

        rows, candidates, brand_counts = [], Counter(), Counter()
        for answer_id, run_date, model, qid, text in answers:
            found = extract_v1(text, patterns)
            for f in found:
                rows.append((answer_id, f["brand"], f["position"], EXTRACTOR, f["alias"]))
                brand_counts[f["brand"]] += 1
            candidates.update(set(unknown_bold_terms(text, patterns)))
            summary = ", ".join(f"{f['position']}.{f['brand']}" for f in found) or "(no brands)"
            print(f"[{run_date} {model} {qid}] {summary}")

        if not args.dry_run:
            with conn.transaction():
                conn.execute("DELETE FROM mentions WHERE extractor = %s", (EXTRACTOR,))
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO mentions (answer_id, brand, position, extractor, matched_alias) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        rows,
                    )

    print(f"\n{len(answers)} answers -> {len(rows)} mentions"
          + (" (dry run, nothing written)" if args.dry_run else f" saved as '{EXTRACTOR}'"))

    print("\nMentions per brand (answers mentioning it):")
    for brand, n in brand_counts.most_common():
        print(f"  {n:>3}  {brand}")

    if candidates:
        print("\nPossible new brands (bold text not in brands.yaml) - add real ones:")
        for term, n in candidates.most_common(25):
            print(f"  {n:>3}  {term}")


if __name__ == "__main__":
    main()
