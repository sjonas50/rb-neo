# rb-neo

A Neo4j knowledge-graph proof-of-concept for **early-childhood (PreK–Grade 3) reading
personalization**. It ingests a corpus of `words/*.json` (each word decomposed into
graphemes, blends, digraphs, syllables, ARPABET phonemes, audio, and a difficulty level),
overlays a per-learner mastery model, and serves a deterministic **next-best-word**
recommender.

> ⚠️ **Compliance:** this PoC runs on **synthetic learners only** — no child PII or audio.
> The June 2025 COPPA amendments treat children's audio as regulated PII and bar its use
> for AI training without separate parental consent; FERPA/SOPIPA require signed DPAs with
> every sub-processor. Do not wire real student data or speech APIs without legal sign-off.
> See [`docs/research.md`](docs/research.md) §Compliance.

## Why a graph?

The value of this corpus is in **shared sub-word units**, not the words themselves. Across a
sample: ~190k words but only **93 distinct sounds** (reused ~445× each), **39 phonemes**, and
~170 graphemes. Modeling those as shared nodes makes the questions that personalize reading —
*"give me words that reinforce `sh` using only units this child already knows"* — a single
graph traversal instead of a self-join nightmare.

On top of the content graph sits a hand-authored **phonics curriculum as a prerequisite DAG**
(`Skill` nodes + `PREREQUISITE_OF` edges, Letters-and-Sounds order): *sh* requires *s* and
*h*; *ar* requires *a* and *r*. A child's **zone of proximal development** is then literally
a query — *unmastered skills whose prerequisites are all mastered* — and each ZPD skill is
ranked by **leverage**: how many real words it would unlock for this child today. That is the
validated intelligent-tutoring pattern (BKT + prerequisite graph + ZPD selection) from
`docs/research.md`, computed deterministically with zero LLM calls.

## Quickstart

```bash
docker compose up -d                      # start Neo4j (http://localhost:7474)
uv venv && uv pip install -e ".[dev]"     # env + deps
uv run rb-neo init                        # apply schema (constraints/indexes)
uv run rb-neo ingest --limit 4000         # parse + load words (omit --limit / use 0 for all)
uv run rb-neo synth                       # create synthetic learners (mastery emerges via BKT)
uv run rb-neo demo                        # run all recommender scenarios
uv run rb-neo explain --learner ava       # structured teacher guidance (LLM if key set, else offline)
uv run pytest -q                          # tests (integration auto-skips without Neo4j)
```

## Showcase web app (the demo)

An investor-facing Streamlit dashboard that makes the value concrete: **the graph
guarantees what's safe to teach; the LLM makes it personal.**

```bash
uv pip install -e ".[showcase]"                # adds streamlit (graphs via built-in Graphviz)
export ANTHROPIC_API_KEY=sk-ant-...            # (or put it in .env) for LIVE lessons
uv run rb-neo ingest --sample 30000            # broad corpus so common words appear
uv run rb-neo synth                            # 4 synthetic kids with interests
uv run streamlit run app/streamlit_app.py      # open http://localhost:8501
```

Three tabs:
- **🎯 Watch the graph decide** — the ZPD loop computed live *on the graph*, with the
  **real Cypher shown at every step**:
  1. the child's position on the **curriculum DAG** — mastered (green), **ZPD** (gold:
     unmastered, every prerequisite met), locked (grey, with the blocking edge in red),
  2. an **animated replay of the traversal on the real subgraph** (vis.js): the mastery
     wave, the prerequisite check, leverage scores, the winning skill fanning out to its
     words, and the i+1 rule accepting/rejecting each one — every frame driven by real
     query rows. Plus the leverage-ranked pool, the Cypher, and Neo4j's own `PROFILE`
     execution plan (operators, rows, db-hits),
  3. i+1 word selection — first as a **funnel of real row counts** (30k words → curated →
     contains the target → all-others-mastered), then per-word letter-chips,
  4. the child masters that one skill → the graph **ripples** out to unlock a wave of
     newly-decodable words — with their rhyme families and minimal pairs for free,
  5. the **graph → AI → graph loop**: the verbatim JSON payload the graph hands Claude
     (persona + safe set), the structured lesson back, and a **graph audit of the AI's
     story** — every word re-checked against the child's mastery, off-curriculum words
     flagged. The same graph that constrains the input verifies the output.
- **🔬 Anatomy of a word** — the full linguistic hierarchy for any word, rendered as a
  layered graph: Word → Syllables → Chunks → Graphemes → Phonemes, with 🔊 on every unit
  that carries its own audio and gold grapheme→phoneme (GPC) edges. The "show every
  relationship between letters, chunks, syllables and sounds" view.
- **🆚 Same skill, two kids** — identical graph-computed safe words for two children →
  two different Claude-written lessons (e.g. soccer vs. space).

> Without `ANTHROPIC_API_KEY`, lessons show a deterministic offline preview so the app still
> runs (good for screenshots); set the key for live generation. Only synthetic data is sent.

## Explore the graph directly (Neo4j Browser)

The database ships with Neo4j Browser. With the stack running, open
<http://localhost:7474> (log in with `NEO4J_USER` / `NEO4J_PASSWORD` from `.env`) and paste
any curated query — the curriculum DAG, shared sub-word units, a learner's mastery overlay,
the ZPD ripple, rhyme families, minimal pairs:

```bash
uv run rb-neo browser-queries                  # print all (copy-pasteable)
uv run rb-neo browser-queries --key curriculum # just one
```

Full write-up with screenshots-worthy queries: [`docs/neo4j-queries.md`](docs/neo4j-queries.md)
(generated from `rb_neo.browser` — the same set the CLI prints).

## Graph schema

```
(:Word {text, prlevel, rwp, rime_key, audio})
  -[:HAS_GRAPHEME {pos}]   -> (:Grapheme {text, key, type, length})  # level A (letter|digraph|blend|...)
  -[:HAS_CHUNK {pos}]      -> (:Chunk {text})                     # level B onset/rime
  -[:HAS_SYLLABLE {pos}]   -> (:Syllable {text})                 # level C
  -[:HAS_SOUND {pos}]      -> (:Sound {id})                      # shared mp3s
  -[:HAS_PHONEME {pos}]    -> (:Phoneme {arpabet, is_vowel})     # 39 shared, ARPABET
  -[:EXEMPLIFIES]          -> (:Pattern {name})                  # CVC, silent_e, has_digraph, ...
  -[:HAS_RIME]             -> (:Rime {key})                      # rhyme via shared node
  -[:MINIMAL_PAIR_OF]      -  (:Word)                            # differ by exactly one phoneme
  -[:APPEARS_IN]           -> (:Sentence {text, audio})          # decodable example sentence

# the linguistic hierarchy — the three decomposition levels nested by char offset:
(:Syllable)-[:CONTAINS_CHUNK]->(:Chunk)-[:CONTAINS_GRAPHEME]->(:Grapheme)

# every level is independently pronounceable, and the phonics correspondence:
(:Grapheme)-[:PRODUCES_SOUND]->(:Sound)        (:Chunk)-[:PRODUCES_SOUND]->(:Sound)
(:Syllable)-[:PRODUCES_SOUND]->(:Sound)
(:Grapheme)-[:MAPS_TO_PHONEME]->(:Phoneme)     # GPC, the atomic unit of phonics (1:1-aligned)
(:Sound)-[:REALIZES]->(:Phoneme)               # the audio that realizes a phoneme

(:Skill {key, kind, phase, seq})                                 # the phonics curriculum
  -[:PREREQUISITE_OF] -> (:Skill)                                # hand-authored DAG (sh ← s, h)
(:Grapheme)-[:IS_SKILL]->(:Skill)                                # grapheme → the skill it teaches

(:Learner {id, name, level})
  -[:ATTEMPTED {ts, correct}] -> (:Word)
  -[:MASTERED {p, attempts, mastered}] -> (:Grapheme)            # BKT posterior per skill
  -[:MASTERED {p, attempts, mastered}] -> (:Skill)               # same posterior on the DAG
```

Graphemes carry ``key = toLower(text)`` so the corpus's case-variant nodes (`Ss`, `Ll`, `A`)
collapse to one skill each; all mastery logic compares keys. The three decomposition levels
(A/B/C) are character-aligned, so the `CONTAINS_*` hierarchy is computed deterministically at
ingest — see the **🔬 Anatomy of a word** tab in the app, or the `anatomy` Browser query.

## What the recommender does (all single Cypher traversals)

| Scenario | Query | Pedagogy |
|---|---|---|
| **ZPD pool** | unmastered skills whose prerequisites are all mastered, ranked by words unlocked | zone of proximal development |
| **Next-best word** | every grapheme mastered except exactly one new target | i+1 scaffolding |
| **Cross-word** | words practicing a target grapheme, all others mastered | decodable practice |
| **Mastery-aware** | fully decodable words (all graphemes mastered) | fluency / confidence |
| **Remediation** | unmastered graphemes missed most in attempts | what to reteach |
| **Review queue** | weakest mastered skills (closest to slipping) | spaced-repetition-lite |
| **Rhyme family / minimal pairs** | shared `Rime` / one-phoneme-apart | phonological awareness |

Routing is **deterministic graph logic** — the optional LLM layer (`agent.py`, Claude Sonnet
4.6 by default) only narrates the choice and generates a decodable sentence; it never decides
what to teach.

## Architecture decisions (PoC pragmatism)

- **BKT is implemented directly** (the classic 4-parameter update) rather than via `pyBKT`, to
  keep the PoC dependency-light. It is the same algorithm pyBKT *fits*; production swaps in
  pyBKT for parameter estimation. See [`docs/research.md`](docs/research.md) §Layer 2.
- **Spaced repetition** is a simple "weakest-mastered-first" queue; production upgrade is FSRS.
- **Speech assessment is out of scope** for this PoC (compliance + child-ASR difficulty); the
  graph + recommender is the high-leverage, zero-risk layer to prove first.

See [`docs/architecture.md`](docs/architecture.md) and [`docs/build-plan.md`](docs/build-plan.md).
