# rb-neo

Neo4j knowledge-graph proof-of-concept for early-childhood (PreK–Grade 3) reading
assessment & personalization.

Ingests a corpus of `words/*.json` (graphemes, blends, digraphs, syllables, ARPABET
phonemes, audio, difficulty levels) into a graph, overlays a per-learner mastery model,
and serves a deterministic **next-best-word** recommender.

> **Compliance:** this POC runs on **synthetic learners only**. No child PII or audio.
> See `docs/research.md` for COPPA/FERPA constraints before using real student data.

## Quickstart

```bash
docker compose up -d                      # start Neo4j (http://localhost:7474)
uv venv && uv pip install -e ".[dev]"     # env + deps
uv run rb-neo init                        # apply schema
uv run rb-neo ingest --limit 4000         # load words
uv run rb-neo synth                       # create synthetic learners
uv run rb-neo demo                        # run recommender scenarios
```

See `docs/architecture.md` and `docs/build-plan.md` for design and phased plan.
