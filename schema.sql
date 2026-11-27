-- Run once in the Supabase SQL editor.
CREATE TABLE IF NOT EXISTS raw_answers (
  id            BIGSERIAL PRIMARY KEY,
  run_id        TEXT NOT NULL,
  run_date      DATE NOT NULL,
  model         TEXT NOT NULL,
  question_id   TEXT NOT NULL,
  question      TEXT NOT NULL,
  answer_text   TEXT,
  input_tokens  INT,
  output_tokens INT,
  cost_usd      NUMERIC(10, 6),
  error         TEXT,
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (run_date, model, question_id)
);
