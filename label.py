"""Hand-label answers in the terminal (ground truth for measuring extraction).

For each unlabeled answer it shows the question + full answer, then v1's guess.
Read the ANSWER first, then fix the guess with short commands:

  Enter          accept the list as shown and save
  -3             remove item 3
  +Ediya         add a brand at the end
  +Ediya@2       add a brand at position 2
  m 4 2          move item 4 to position 2
  s 1 p          sentiment of item 1: p=positive, n=neutral, x=negative
  s ppnpx        sentiment of ALL items at once, one letter per item in order
  s all p        same sentiment for every item
  skip           skip this answer for now
  q              quit (everything saved so far stays saved)

Usage:  python label.py            # label unlabeled answers, oldest first
        python label.py --limit 10
"""
import argparse
import os
import textwrap

import psycopg
import yaml
from dotenv import load_dotenv

from extract import compile_patterns, extract_v1, normalize

SENTIMENT = {"p": "positive", "n": "neutral", "x": "negative"}


def canonical(name: str, brands: dict, patterns) -> str:
    """Map user input ('twosome', '빽다방', 'mega coffee') to the canonical brand name."""
    for brand in brands:
        if normalize(brand) == normalize(name):
            return brand
    found = extract_v1(name, patterns)
    if found:
        return found[0]["brand"]
    print(f"  (note: '{name}' is not in brands.yaml - saved as typed; consider adding it)")
    return name.strip()


def show(items):
    if not items:
        print("  (no brands)")
    for i, it in enumerate(items, 1):
        s = f"  [{it['sentiment']}]" if it.get("sentiment") else ""
        print(f"  {i}. {it['brand']}{s}")


def edit_loop(items, brands, patterns):
    """Returns the final list, 'skip', or 'quit'."""
    while True:
        cmd = input("> ").strip()
        try:
            if cmd == "":
                return items
            if cmd == "skip":
                return "skip"
            if cmd == "q":
                return "quit"
            if cmd.startswith("-") and cmd[1:].isdigit():
                items.pop(int(cmd[1:]) - 1)
            elif cmd.startswith("+"):
                name, _, pos = cmd[1:].partition("@")
                brand = canonical(name, brands, patterns)
                if any(it["brand"] == brand for it in items):
                    print(f"  {brand} is already in the list")
                    continue
                idx = int(pos) - 1 if pos else len(items)
                items.insert(idx, {"brand": brand, "sentiment": None})
            elif cmd.startswith("m "):
                _, a, b = cmd.split()
                items.insert(int(b) - 1, items.pop(int(a) - 1))
            elif cmd.startswith("s "):
                parts = cmd.split()
                if len(parts) == 3 and parts[1] == "all":          # s all p
                    for it in items:
                        it["sentiment"] = SENTIMENT[parts[2]]
                elif len(parts) == 3:                              # s 2 p
                    items[int(parts[1]) - 1]["sentiment"] = SENTIMENT[parts[2]]
                elif len(parts) == 2 and set(parts[1]) <= set(SENTIMENT):  # s ppnpx
                    letters = parts[1]
                    if len(letters) != len(items):
                        print(f"  ? {len(letters)} letters for {len(items)} brands - give one letter per brand")
                        continue
                    for it, ch in zip(items, letters):
                        it["sentiment"] = SENTIMENT[ch]
                else:
                    raise ValueError
            else:
                print("  ? commands: Enter, -N, +Brand, +Brand@N, m A B, s N p/n/x, s ppnx, s all p, skip, q")
                continue
        except (ValueError, IndexError, KeyError):
            print("  ? couldn't apply that - check the item number / command")
            continue
        show(items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    load_dotenv()
    with open("brands.yaml", encoding="utf-8") as f:
        brands = yaml.safe_load(f)
    patterns = compile_patterns(brands)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        answers = conn.execute(
            """SELECT r.id, r.run_date, r.model, r.question, r.answer_text
               FROM raw_answers r
               LEFT JOIN labeled_answers l ON l.answer_id = r.id
               WHERE r.error IS NULL AND r.answer_text IS NOT NULL AND l.answer_id IS NULL
               ORDER BY r.id LIMIT %s""",
            (args.limit,),
        ).fetchall()
        done = conn.execute("SELECT count(*) FROM labeled_answers").fetchone()[0]
        print(f"{done} answers labeled so far, {len(answers)} to go in this session.\n")

        for n, (answer_id, run_date, model, question, text) in enumerate(answers, 1):
            print("=" * 80)
            print(f"[{n}/{len(answers)}] answer #{answer_id} - {run_date} {model}")
            print(f"Q: {question}\n")
            for para in text.splitlines():
                print(textwrap.fill(para, 100) if para.strip() else "")
            print("\n--- v1 guess (fix it if needed) ---")
            items = [{"brand": f["brand"], "sentiment": None} for f in extract_v1(text, patterns)]
            show(items)

            result = edit_loop(items, brands, patterns)
            if result == "quit":
                break
            if result == "skip":
                continue

            with conn.transaction():
                conn.execute("DELETE FROM labels WHERE answer_id = %s", (answer_id,))
                for pos, it in enumerate(result, 1):
                    conn.execute(
                        "INSERT INTO labels (answer_id, brand, position, sentiment) VALUES (%s,%s,%s,%s)",
                        (answer_id, it["brand"], pos, it["sentiment"]),
                    )
                conn.execute(
                    "INSERT INTO labeled_answers (answer_id) VALUES (%s) "
                    "ON CONFLICT (answer_id) DO UPDATE SET labeled_at = now()",
                    (answer_id,),
                )
            print(f"  saved {len(result)} brands for answer #{answer_id}\n")

    print("\nDone. Run `python accuracy.py` to see how v1 scores against your labels.")


if __name__ == "__main__":
    main()
