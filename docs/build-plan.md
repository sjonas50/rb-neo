# Build Plan: rb-neo

_Phased, gated build. Each phase has a runnable test gate that must pass before advancing._
_Stack per `docs/research.md`; architecture per `docs/architecture.md`._

**Compliance gate (applies to every phase):** synthetic learners only. No child PII/audio. Anthropic calls (Phase 4) use synthetic/non-PII content.

**Tooling:** `uv` for env/deps, `ruff` format+lint, `pytest`. Neo4j via `docker compose up -d`.

---

## Phase 0 — Scaffold & config — **S — DONE**

Project skeleton, dependencies, config, DB wrapper, base models, parser.

- [x] `pyproject.toml` (src layout, deps: neo4j, pydantic, pydantic-settings, structlog, typer), `docker-compose.yml`, `.env.example`, `.gitignore`
- [x] `src/rb_neo/config.py`, `logging.py`, `db.py` (schema constraints/indexes, batch helpers, reset)
- [x] `src/rb_neo/models.py`, `parsing.py` (grapheme/blend/digraph classification, phonemes, patterns, rime)

**Gate:** `uv run ruff check src && uv run python -c "from rb_neo.parsing import parse_word_file; print('ok')"`

---

## Phase 1 — Content-graph ingestion + derived edges — **M**

Load the word corpus into Neo4j idempotently; compute pedagogy edges.

- [ ] `src/rb_neo/ingest.py` — `ingest_words(db, records, batch_size=500)`: UNWIND/MERGE for `Word`, `Grapheme`(+`PRODUCES_SOUND`→`Sound`), `Phoneme`, `Chunk`(B), `Syllable`(C), `Pattern`(`EXEMPLIFIES`), `Rime`(`HAS_RIME`)
- [ ] `derive_minimal_pairs(records)` — phoneme-wildcard hashing → `MINIMAL_PAIR_OF` edges (cap per bucket)
- [ ] `src/rb_neo/cli.py` — `init` (apply schema), `ingest --limit/--all`, `reset`
- [ ] `tests/test_ingest.py` — load ~200 words into a test DB (or skip if no Neo4j), assert node/rel counts and shared-node reuse (`cat` & `cot` share Grapheme `c`)

**Gate:** `uv run rb-neo init && uv run rb-neo ingest --limit 500 && uv run pytest tests/test_ingest.py -q`

---

## Phase 2 — Learner overlay + BKT mastery — **M**

Model what a learner knows; update from attempts.

- [ ] `src/rb_neo/models.py` — add `Learner`, `Attempt`, `MasteryEstimate`
- [ ] `src/rb_neo/mastery.py` — `update_from_attempts(db, learner_id)` using `pyBKT` per skill (grapheme/phoneme); exponential **decay fallback** (~0.08/day) before fit; write `HAS_MASTERY {p, attempts, updated}` edges
- [ ] `is_mastered(p, threshold=0.85)` helper; grade-band thresholds
- [ ] `tests/test_mastery.py` — feed a synthetic attempt sequence, assert posterior rises with correct streak, decays without practice

**Gate:** `uv run pytest tests/test_mastery.py -q`

---

## Phase 3 — ZPD recommender + synthetic students + demo — **L**

The payoff: next-best-word and friends, with data to prove it.

- [ ] `src/rb_neo/synthetic.py` — `seed_learners(db, seed=42)`: 3 profiles (beginner/mid/advanced) along a phonics scope/sequence; `MASTERED` edges + simulated `ATTEMPTED` history (deterministic timestamps)
- [ ] `src/rb_neo/recommend.py` — `next_best_word` (all graphemes mastered except exactly one new target, ranked by reuse+level), `cross_word` (practice a target grapheme using otherwise-mastered words), `mastery_aware` (fully-decodable fluency set), `remediation` (graphemes most-missed in `ATTEMPTED`), `rhyme_family`, `minimal_pairs`; FSRS review ordering via `py-fsrs`
- [ ] `cli.py` — `synth`, `demo` (runs all scenarios per learner, pretty-prints)
- [ ] `tests/test_recommend.py` — against a seeded fixture: next-best-word introduces exactly one unmastered grapheme; mastery-aware set is fully decodable

**Gate:** `uv run rb-neo ingest --limit 4000 && uv run rb-neo synth && uv run rb-neo demo && uv run pytest tests/test_recommend.py -q`

---

## Phase 4 — LLM agent layer (optional, structured) — **M**

Turn graph picks into teacher-facing rationale + decodable content.

- [ ] `src/rb_neo/agent.py` — Anthropic SDK, **Claude Haiku 4.5**, `tool_use` for structured `Recommendation` (target_skill, words, rationale, decodable_sentence); prompt-cache curriculum context
- [ ] Graceful no-op when `ANTHROPIC_API_KEY` unset (demo still works without LLM)
- [ ] `cli.py` — `explain --learner <id>` wraps `next_best_word` output
- [ ] `tests/test_agent.py` — mocked client; assert schema-valid structured output, no network in tests

**Gate:** `uv run pytest tests/test_agent.py -q`

---

## Phase 5 — Hardening & docs — **M**

- [ ] Error handling on driver/connection; clear message when Neo4j is down
- [ ] structlog throughout; no bare prints
- [ ] `README.md` — quickstart (docker compose up → init → ingest → synth → demo), schema diagram, compliance note
- [ ] `Dockerfile` for the CLI; `ruff format` clean; type hints complete
- [ ] `tests/conftest.py` — shared fixtures; skip-if-no-Neo4j marker

**Gate:** `uv run ruff check src && uv run ruff format --check src && uv run pytest -q`

---

## Summary

| Phase | Title | Complexity | Status |
|---|---|---|---|
| 0 | Scaffold & config | S | done |
| 1 | Content-graph ingestion + derived edges | M | next |
| 2 | Learner overlay + BKT mastery | M | |
| 3 | ZPD recommender + synthetic students + demo | L | |
| 4 | LLM agent layer (optional) | M | |
| 5 | Hardening & docs | M | |

**~24 tasks across 6 phases.** Critical path to a runnable demo ends at Phase 3.
