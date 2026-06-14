# CLAUDE.md — rb-neo

## Project
**rb-neo** — a Neo4j knowledge-graph proof-of-concept for an early-childhood (PreK–Grade 3) reading
assessment & personalization platform. Ingests an existing corpus of ~190k `words/*.json` files
(each decomposed into graphemes, blends, digraphs, syllables, ARPABET phonemes, audio, difficulty
level) into a graph, overlays a per-learner mastery model (BKT), and serves a deterministic
**next-best-word** recommender. See `docs/research.md`, `docs/architecture.md`, `docs/build-plan.md`.

## Commands
```bash
docker compose up -d                 # start Neo4j (browser http://localhost:7474)
uv venv && uv pip install -e ".[dev]" # env + deps
uv run rb-neo init                   # apply schema (constraints/indexes)
uv run rb-neo ingest --limit 4000    # load words (omit limit / use --all for full)
uv run rb-neo synth                  # create synthetic learners
uv run rb-neo demo                   # run recommender scenarios
uv run pytest -q                     # tests
uv run ruff check src && uv run ruff format src
```

## Architecture decisions
- **Neo4j** is the right tool *because the value is in shared sub-word units*: 93 sounds, 39 phonemes,
  ~170 graphemes are reused across 190k words (~445× avg). MERGE-on-key keeps them singletons.
- **Routing is deterministic Cypher, not an LLM.** "Next skill whose prerequisites are mastered" and
  "next-best-word (reuse mastered units, introduce ≤1 new grapheme)" are graph queries. The LLM
  (Claude Sonnet 4.6 by default; Haiku 4.5 for cheaper scale — Phase 4) only narrates/generates content.
- **The ZPD is a query over a prerequisite DAG.** `curriculum.py` hand-authors the phonics scope &
  sequence as `Skill` nodes + `PREREQUISITE_OF` edges (sh ← s, h). ZPD = unmastered skills with all
  prerequisites mastered, ranked by leverage (curated words each would unlock). This is the showcase hero.
- **Grapheme case variants collapse via `key`.** The corpus has `Ss`/`Ll`/`A` nodes; all mastery and
  decodability logic compares `g.key = toLower(g.text)`. MASTERED edges are written per-key to every
  variant node and to the Skill node.
- **Mastery = pyBKT per skill node**, with an exponential decay fallback before fit. DKT only at scale.
- **Derived edges** computed at ingest: `Rime` nodes (not pairwise RHYMES_WITH) and `MINIMAL_PAIR_OF`
  via phoneme-wildcard hashing (O(n·L), not O(n²)).
- **Full linguistic hierarchy** (the source's three decomposition levels, char-aligned): Syllable
  `-CONTAINS_CHUNK->` Chunk `-CONTAINS_GRAPHEME->` Grapheme. Each level carries `PRODUCES_SOUND`
  (audsounds/B/C). Grapheme `-MAPS_TO_PHONEME->` Phoneme (GPC, 1:1-aligned only) and Sound
  `-REALIZES->` Phoneme. `parsing.align_containment` / `align_gpc` compute these offline.

## File structure
```
src/rb_neo/
  config.py      # pydantic-settings from .env
  logging.py     # structlog
  db.py          # Neo4jDB wrapper, schema constraints/indexes, batch helpers
  models.py      # Pydantic: WordRecord, GraphemeUnit, ... Learner, Attempt
  parsing.py     # word JSON -> WordRecord; grapheme/blend/digraph classification, patterns, rime
  ingest.py      # batch UNWIND/MERGE loader + derived edges          (Phase 1)
  curriculum.py  # phonics scope/sequence as Skill DAG (PREREQUISITE_OF)
  wordlists.py   # curated decodable words tagged common=true
  traverse.py    # narrated showcase steps (Cypher + viz per step) + query funnel
  traversal_player.py  # vis.js animated replay of the ZPD traversal (real query data)
  mastery.py     # BKT mastery updates                                 (Phase 2)
  recommend.py   # ZPD queries: next_best_word, cross_word, ...        (Phase 3)
  synthetic.py   # synthetic learners                                  (Phase 3)
  agent.py       # Claude structured rationale/content (optional)      (Phase 4)
  cli.py         # typer CLI
tests/           # pytest (skip-if-no-Neo4j marker)
words/           # source corpus (gitignored in real use)
docs/            # research.md, architecture.md, build-plan.md
```

## Conventions
Python 3.11+, type hints on all signatures, Google docstrings, async only where it pays.
Pydantic v2 for all schemas. structlog (never bare print). uv for deps. Conventional commits.

## Pitfalls (from research)
- **COMPLIANCE IS A BLOCKER.** Synthetic learners only in this POC. No child audio/PII. COPPA (June
  2025) treats child audio as PII and bars AI-training use without separate parental consent; FERPA/
  SOPIPA require signed DPAs with every sub-processor before student data flows. Don't wire real
  student data or speech APIs without legal sign-off.
- **Child ASR is the hard part later** (2–5× worse than adult; miscue F1 ~0.52). Deferred: Azure
  Pronunciation Assessment (managed) / self-hosted fine-tuned Whisper. Build the miscue classifier
  in-house — no API does it.
- **Kùzu is dead** (Apple acquisition, archived Oct 2025) — do not adopt.
- **Word filenames are URL-encoded**, and `=` separates a written symbol from its spoken word
  (`100%3Dhundred` → written `100` / spoken `hundred`). `parsing.decode_filename` handles this.
- **Grapheme case is meaningful** in the source (`A`, `Ll`, `aArd`) — preserve original text, lowercase
  only for classification.
- Use the `neo4j` package (not deprecated `neo4j-driver`); driver 6.x.
```
