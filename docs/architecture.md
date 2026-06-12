# Architecture: rb-neo

_Early-childhood reading personalization on a Neo4j knowledge graph (PreK–Grade 3)._
_Last updated: 2026-06-11. Derived from `docs/research.md`._

## 1. System Overview

rb-neo is a **content knowledge graph** of the existing word corpus plus a **learner-model overlay** and a **deterministic ZPD recommender**, with an optional LLM layer for rationale/content. The graph is the source of truth for *what to teach next*; Bayesian Knowledge Tracing (BKT) holds *what the child knows*; Cypher answers *next-best-word* with zero LLM calls.

**Compliance constraint (hard):** the POC operates on **synthetic learners only**. No child audio, no PII, no third-party AI calls on student data until DPAs + parental-consent flow exist (see research §Compliance).

```
                         ┌───────────────────────────────────────────────┐
                         │                 words/*.json                   │
                         │  (114k+ words: graphemes, blends, digraphs,    │
                         │   syllables, ARPABET phonemes, audio, levels)  │
                         └───────────────────────┬───────────────────────┘
                                                 │  parse_word_file()
                                                 ▼
   ┌─────────────┐   apply_schema()   ┌─────────────────────────────────┐
   │  config /   │ ─────────────────▶ │           Neo4j graph           │
   │  .env       │                    │  CONTENT:  Word, Grapheme,      │
   └─────────────┘                    │   Sound, Phoneme, Chunk,        │
                                      │   Syllable, Pattern, Rime       │
   ┌─────────────┐  synth_learners()  │  LEARNER:  Learner              │
   │ synthetic   │ ─────────────────▶ │   -[:MASTERED]->(skill)         │
   │ students    │                    │   -[:ATTEMPTED]->(Word)         │
   └─────────────┘                    └───────────────┬─────────────────┘
                                                       │ Cypher
                       ┌───────────────────────────────┼───────────────────────────────┐
                       ▼                                ▼                                ▼
              ┌─────────────────┐            ┌────────────────────┐          ┌────────────────────┐
              │  BKT mastery    │            │  ZPD recommender   │          │  LLM agent (opt.)  │
              │  (pyBKT)        │            │  next_best_word    │          │  Claude Haiku 4.5  │
              │  attempt->update│            │  cross_word        │          │  tool_use struct.  │
              │  decay fallback │            │  mastery_aware     │          │  rationale/content │
              └─────────────────┘            │  remediation       │          └────────────────────┘
                                             │  + FSRS review     │
                                             └────────────────────┘
                                                       │
                                                       ▼
                                              typer CLI: `rb-neo demo`
```

## 2. Component Breakdown

| Component | File | Purpose | Inputs | Outputs |
|---|---|---|---|---|
| Config | `config.py` | Settings from env/.env (pydantic-settings) | env vars | `Settings` |
| Logging | `logging.py` | structlog console setup | level | bound logger |
| DB wrapper | `db.py` | Driver lifecycle, schema constraints/indexes, batch write helpers, reset | `Settings` | `Neo4jDB` |
| Models | `models.py` | Pydantic schemas for parsed words/units | — | `WordRecord` etc. |
| Parser | `parsing.py` | Decode filename + JSON → graphemes/phonemes/patterns/rime; classify blends/digraphs | `words/*.json` | `WordRecord` |
| Ingester | `ingest.py` | Idempotent UNWIND/MERGE batch load; derived edges (rime, minimal pairs) | `list[WordRecord]` | graph nodes/rels |
| Mastery | `mastery.py` | BKT per skill; update from attempts; decay fallback; write `HAS_MASTERY` | attempts | mastery probs |
| Recommender | `recommend.py` | ZPD Cypher queries: next-best-word, cross-word, mastery-aware, remediation; FSRS review | `learner_id`, params | ranked words |
| Synthetic | `synthetic.py` | Generate `Learner` nodes w/ scope-sequence mastery + attempt history | seed, profiles | learner subgraph |
| LLM agent | `agent.py` (Phase 4) | Wrap graph results → structured rationale/decodable content | graph context | `Recommendation` |
| CLI | `cli.py` | typer commands: init, ingest, synth, demo, reset | args | console |

## 3. Data Flow (next-best-word, the core loop)

1. `rb-neo ingest --limit N` → parse word files → batch MERGE content graph + derived edges.
2. `rb-neo synth` → create synthetic `Learner` nodes; for each, MASTERED edges to a prefix of the phonics scope/sequence + simulated `ATTEMPTED` history.
3. `mastery.update_from_attempts(learner)` → BKT posterior per skill → MERGE `HAS_MASTERY {p, attempts}`.
4. `recommend.next_best_word(learner)` → Cypher: words whose graphemes are all mastered **except exactly one** new target, ordered by mastered-unit reuse + level → returns ranked list.
5. (Optional) `agent.explain(learner, words)` → Claude Haiku structured output: teacher rationale + a decodable sentence.
6. `rb-neo demo` prints scenarios: next-best-word, cross-word reinforcement, mastery-aware fluency set, remediation from errors, rhyme family, minimal pairs.

## 4. External Dependencies & Auth

| Dependency | Auth | Used in | Notes |
|---|---|---|---|
| Neo4j (Docker / AuraDB) | basic auth (user/pass) | all | local via docker-compose for POC |
| Anthropic API | `ANTHROPIC_API_KEY` | Phase 4 only | synthetic/non-PII content only; sign DPA before any student data |
| pyBKT, py-fsrs | none (local) | mastery, recommender | MIT |

**Deferred (post-POC, compliance-gated):** Azure Pronunciation Assessment, self-hosted Whisper. Not in scope for this POC.

## 5. Environment Variables

| Var | Default | Purpose |
|---|---|---|
| `NEO4J_URI` | `bolt://localhost:7687` | driver target |
| `NEO4J_USER` | `neo4j` | auth |
| `NEO4J_PASSWORD` | `rbneopass` | auth |
| `RB_WORDS_DIR` | `words` | source corpus dir |
| `ANTHROPIC_API_KEY` | _unset_ | Phase 4 LLM (optional) |

## 6. Scaling Considerations

- **Ingest:** 190k word files. Batch UNWIND/MERGE in chunks of ~500 within managed write txns; uniqueness constraints back the MERGEs. POC default `--limit 8000`; `--all` for full load. Parsing is embarrassingly parallel if needed.
- **Shared-node reuse is the win:** 93 sounds, 39 phonemes, ~170 graphemes are massively shared — MERGE-on-key keeps them singletons; the graph stays small relative to word count.
- **Derived edges:** minimal pairs via phoneme-wildcard hashing (O(n·L)), not O(n²); rime via shared `Rime` nodes (avoids pairwise `RHYMES_WITH` explosion).
- **Mastery at classroom scale:** BKT update is per-attempt and cheap; store posteriors on `HAS_MASTERY` edges. DKT only at >10k sessions.
- **LLM cost:** prompt-cache the curriculum context; Haiku ~$72/classroom/yr (research §Layer 4). Graph answers routing with no LLM call.
- **Production:** AuraDB Professional or self-hosted Neo4j Enterprise (RBAC/clustering/data sovereignty); GDS for skill clustering (Node2Vec/Louvain).
