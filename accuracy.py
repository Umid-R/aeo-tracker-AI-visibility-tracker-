"""Score an extractor against your hand labels.

Only answers in labeled_answers are scored.
  precision = correct brands / brands the extractor found   (how often it's right)
  recall    = correct brands / brands in your labels         (how much it catches)
  position  = share of correct brands placed at the right position

Usage:  python accuracy.py                      # v1_string
        python accuracy.py --extractor v2_llm
"""
import argparse
import os
from collections import Counter

import psycopg
from dotenv import load_dotenv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", default="v1_string")
    args = ap.parse_args()
    load_dotenv()

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        labeled = [r[0] for r in conn.execute("SELECT answer_id FROM labeled_answers")]
        if not labeled:
            print("No labeled answers yet - run `python label.py` first.")
            return
        if args.extractor != "v1_string":   # only score answers this extractor processed
            done = {r[0] for r in conn.execute(
                "SELECT answer_id FROM extracted_answers WHERE extractor = %s", (args.extractor,))}
            labeled = [a for a in labeled if a in done]
            if not labeled:
                print(f"No labeled answers processed by {args.extractor} yet.")
                return
        truth = conn.execute(
            "SELECT answer_id, brand, position, sentiment FROM labels WHERE answer_id = ANY(%s)", (labeled,)
        ).fetchall()
        pred = conn.execute(
            "SELECT answer_id, brand, position, sentiment FROM mentions "
            "WHERE extractor = %s AND answer_id = ANY(%s)", (args.extractor, labeled)
        ).fetchall()

    t = {(a, b): p for a, b, p, _ in truth}
    p = {(a, b): pos for a, b, pos, _ in pred}
    t_sent = {(a, b): s for a, b, _, s in truth if s}
    p_sent = {(a, b): s for a, b, _, s in pred}
    correct = t.keys() & p.keys()
    missed = Counter(b for (_, b) in t.keys() - p.keys())
    wrong = Counter(b for (_, b) in p.keys() - t.keys())
    same_pos = sum(1 for k in correct if t[k] == p[k])

    precision = len(correct) / len(p) if p else 0
    recall = len(correct) / len(t) if t else 0
    print(f"Extractor: {args.extractor}   labeled answers: {len(labeled)}")
    print(f"  true brands {len(t)} | found {len(p)} | correct {len(correct)}")
    print(f"  precision  {precision:.1%}")
    print(f"  recall     {recall:.1%}")
    if correct:
        print(f"  position   {same_pos / len(correct):.1%} of correct brands at the right position")
    scored = [k for k in correct if k in t_sent and p_sent.get(k)]
    if scored:
        agree = sum(1 for k in scored if t_sent[k] == p_sent[k])
        print(f"  sentiment  {agree / len(scored):.1%} agree with your labels ({len(scored)} brands with labeled sentiment)")
    if missed:
        print("\nMissed (in labels, not found) - add aliases?")
        for b, n in missed.most_common():
            print(f"  {n:>3}  {b}")
    if wrong:
        print("\nWrong (found, not in labels) - false matches?")
        for b, n in wrong.most_common():
            print(f"  {n:>3}  {b}")


if __name__ == "__main__":
    main()
