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

Two tabs:
- **🎯 Watch the graph decide** — a guided traversal where the recommendation is computed
  *on the graph*, with the **real Cypher shown at every step**:
  1. what the graph knows about the child (mastered graphemes),
  2. the graph evaluates every candidate word and picks the next skill — the rule is
     *visible* (accepted = exactly one new red letter; rejected words show why),
  3. the child masters that one grapheme → the graph **ripples** out to unlock a wave of
     newly-decodable words (before/after count),
  4. the LLM writes a personalized lesson from the graph-guaranteed safe set.
- **🆚 Same skill, two kids** — identical graph-computed safe words for two children →
  two different Claude-written lessons (e.g. soccer vs. space).

> Without `ANTHROPIC_API_KEY`, lessons show a deterministic offline preview so the app still
> runs (good for screenshots); set the key for live generation. Only synthetic data is sent.

## Graph schema

```
(:Word {text, prlevel, rwp, rime_key, audio})
  -[:HAS_GRAPHEME {pos}]   -> (:Grapheme {text, type, length})   # letter|digraph|blend|r_controlled|vowel_team|morpheme
  -[:HAS_SOUND {pos}]      -> (:Sound {id})                      # 93 shared mp3s
  -[:HAS_PHONEME {pos}]    -> (:Phoneme {arpabet, is_vowel})     # 39 shared
  -[:HAS_CHUNK {pos}]      -> (:Chunk {text})                    # level-B onset/rime
  -[:HAS_SYLLABLE {pos}]   -> (:Syllable {text})                 # level-C
  -[:EXEMPLIFIES]          -> (:Pattern {name})                  # CVC, silent_e, has_digraph, ...
  -[:HAS_RIME]             -> (:Rime {key})                      # rhyme via shared node
  -[:MINIMAL_PAIR_OF]      -  (:Word)                            # differ by exactly one phoneme

(:Grapheme)-[:PRODUCES_SOUND]->(:Sound)

(:Learner {id, name, level})
  -[:ATTEMPTED {ts, correct}] -> (:Word)
  -[:MASTERED {p, attempts, mastered}] -> (:Grapheme)            # BKT posterior per skill
```

## What the recommender does (all single Cypher traversals)

| Scenario | Query | Pedagogy |
|---|---|---|
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
