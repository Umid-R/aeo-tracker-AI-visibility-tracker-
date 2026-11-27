"""Daily run: ask every question to every model and save the raw answers.

Usage:  python run.py
Safe to re-run on the same day: existing (date, model, question) rows are skipped.
"""
import datetime
import os
import time
import uuid

import psycopg
import yaml
from dotenv import load_dotenv

from providers import PROVIDERS

MAX_RETRIES = 3

INSERT_SQL = """
    INSERT INTO raw_answers (run_id, run_date, model, question_id, question,
                             answer_text, input_tokens, output_tokens, cost_usd, error)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (run_date, model, question_id) DO NOTHING
"""

EXISTS_SQL = """
    SELECT 1 FROM raw_answers
    WHERE run_date = %s AND model = %s AND question_id = %s AND error IS NULL
"""


def call_with_retry(fn, model_name, question):
    """Retry on errors (e.g. 429 rate limit) with growing waits: 5s, 10s, 20s."""
    for attempt in range(MAX_RETRIES):
        try:
            return fn(model_name, question), None
        except Exception as e:  # noqa: BLE001 - we want to log any API failure
            if attempt == MAX_RETRIES - 1:
                return None, f"{type(e).__name__}: {e}"
            time.sleep(5 * 2 ** attempt)


def main():
    load_dotenv()
    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    today = datetime.date.today()
    run_id = f"{today}-{uuid.uuid4().hex[:6]}"
    pause = cfg.get("sleep_between_calls", 0)
    ok = failed = skipped = 0

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        for model in cfg["models"]:
            ask = PROVIDERS[model["provider"]]
            for q in cfg["questions"]:
                if conn.execute(EXISTS_SQL, (today, model["name"], q["id"])).fetchone():
                    skipped += 1
                    continue

                result, error = call_with_retry(ask, model["name"], q["text"])
                if result:
                    text, tin, tout = result
                    cost = ((tin or 0) * model["price_in"] + (tout or 0) * model["price_out"]) / 1_000_000
                else:
                    text = tin = tout = cost = None

                # A failed row is replaced if a later run the same day succeeds
                if error is None:
                    conn.execute(
                        "DELETE FROM raw_answers WHERE run_date=%s AND model=%s AND question_id=%s",
                        (today, model["name"], q["id"]),
                    )
                conn.execute(INSERT_SQL, (run_id, today, model["name"], q["id"], q["text"],
                                          text, tin, tout, cost, error))

                if error:
                    failed += 1
                    print(f"[FAIL] {model['name']} {q['id']}: {error}")
                else:
                    ok += 1
                    print(f"[ok]   {model['name']} {q['id']} ({tout} tokens)")
                time.sleep(pause)

    print(f"\nrun {run_id}: {ok} ok, {failed} failed, {skipped} already done today")


if __name__ == "__main__":
    main()
