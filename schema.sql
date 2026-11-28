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

-- Brands extracted from each answer. One row per (answer, brand, extractor).
CREATE TABLE IF NOT EXISTS mentions (
  id            BIGSERIAL PRIMARY KEY,
  answer_id     BIGINT NOT NULL REFERENCES raw_answers(id) ON DELETE CASCADE,
  brand         TEXT NOT NULL,
  position      INT,            -- 1 = mentioned first
  sentiment     TEXT,           -- positive / neutral / negative (filled by v2)
  extractor     TEXT NOT NULL,  -- 'v1_string', 'v2_llm', ...
  matched_alias TEXT,           -- which spelling matched (v1), for debugging
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (answer_id, brand, extractor)
);
CREATE INDEX IF NOT EXISTS mentions_brand_idx ON mentions (brand);
CREATE INDEX IF NOT EXISTS mentions_answer_idx ON mentions (answer_id);

-- Hand-checked "true" brands per answer (ground truth for accuracy).
CREATE TABLE IF NOT EXISTS labels (
  id         BIGSERIAL PRIMARY KEY,
  answer_id  BIGINT NOT NULL REFERENCES raw_answers(id) ON DELETE CASCADE,
  brand      TEXT NOT NULL,
  position   INT,
  sentiment  TEXT CHECK (sentiment IN ('positive', 'neutral', 'negative')),
  labeled_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (answer_id, brand)
);
CREATE INDEX IF NOT EXISTS labels_answer_idx ON labels (answer_id);

-- Which answers have been labeled (also covers answers with zero brands).
CREATE TABLE IF NOT EXISTS labeled_answers (
  answer_id  BIGINT PRIMARY KEY REFERENCES raw_answers(id) ON DELETE CASCADE,
  labeled_at TIMESTAMPTZ DEFAULT now(),
  note       TEXT
);

-- Which answers each extractor has already processed (incl. answers with zero brands),
-- so quota-limited extractors (v2_llm) never redo work.
CREATE TABLE IF NOT EXISTS extracted_answers (
  answer_id    BIGINT NOT NULL REFERENCES raw_answers(id) ON DELETE CASCADE,
  extractor    TEXT NOT NULL,
  model        TEXT,
  extracted_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (answer_id, extractor)
);
