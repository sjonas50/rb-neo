"""Idempotent batch ingestion of parsed words into Neo4j, plus derived edges.

All writes use ``MERGE`` on stable keys so re-running is safe. Shared sub-word
units (sounds, phonemes, graphemes) collapse to singleton nodes — that reuse is
the whole point of modelling this corpus as a graph.
"""

from __future__ import annotations

import collections
from pathlib import Path

from .db import Neo4jDB, chunked
from .logging import get_logger
from .models import WordRecord
from .parsing import iter_word_files, parse_word_file

log = get_logger()

# -- Cypher statements (one focused statement per relationship family) -----------

_WORDS = """
UNWIND $batch AS w
MERGE (word:Word {text: w.text})
SET word.written = w.written,
    word.spoken = w.spoken,
    word.prlevel = w.prlevel,
    word.rwp = w.rwp,
    word.syllables_raw = w.syllables_raw,
    word.audio = w.audio,
    word.rime_key = w.rime_key
"""

_GRAPHEMES = """
UNWIND $batch AS w
UNWIND w.graphemes AS g
MATCH (word:Word {text: w.text})
MERGE (gr:Grapheme {text: g.text})
  ON CREATE SET gr.type = g.type, gr.length = g.length
MERGE (word)-[:HAS_GRAPHEME {pos: g.pos}]->(gr)
FOREACH (_ IN CASE WHEN g.sound <> '' THEN [1] ELSE [] END |
  MERGE (s:Sound {id: g.sound})
  MERGE (gr)-[:PRODUCES_SOUND]->(s)
  MERGE (word)-[:HAS_SOUND {pos: g.pos}]->(s)
)
"""

_PHONEMES = """
UNWIND $batch AS w
UNWIND w.phonemes AS p
MATCH (word:Word {text: w.text})
MERGE (ph:Phoneme {arpabet: p.text})
  ON CREATE SET ph.is_vowel = p.is_vowel
MERGE (word)-[:HAS_PHONEME {pos: p.pos}]->(ph)
"""

# Level B -> Chunk, Level C -> Syllable. Same payload shape, two labels.
_CHUNKS = """
UNWIND $batch AS w
UNWIND [c IN w.chunks WHERE c.level = 'B'] AS c
MATCH (word:Word {text: w.text})
MERGE (ch:Chunk {text: c.text})
MERGE (word)-[:HAS_CHUNK {pos: c.pos}]->(ch)
"""

_SYLLABLES = """
UNWIND $batch AS w
UNWIND [c IN w.chunks WHERE c.level = 'C'] AS c
MATCH (word:Word {text: w.text})
MERGE (sy:Syllable {text: c.text})
MERGE (word)-[:HAS_SYLLABLE {pos: c.pos}]->(sy)
"""

_PATTERNS = """
UNWIND $batch AS w
UNWIND w.patterns AS pname
MATCH (word:Word {text: w.text})
MERGE (pt:Pattern {name: pname})
MERGE (word)-[:EXEMPLIFIES]->(pt)
"""

_RIME = """
UNWIND [w IN $batch WHERE w.rime_key <> ''] AS w
MATCH (word:Word {text: w.text})
MERGE (r:Rime {key: w.rime_key})
MERGE (word)-[:HAS_RIME]->(r)
"""

_MINIMAL_PAIRS = """
UNWIND $batch AS pair
MATCH (a:Word {text: pair.a}), (b:Word {text: pair.b})
MERGE (a)-[:MINIMAL_PAIR_OF]-(b)
"""

_STATEMENTS = [_WORDS, _GRAPHEMES, _PHONEMES, _CHUNKS, _SYLLABLES, _PATTERNS, _RIME]


def _record_to_payload(rec: WordRecord) -> dict:
    """Flatten a :class:`WordRecord` into a Cypher-friendly parameter map."""
    return {
        "text": rec.text,
        "written": rec.written,
        "spoken": rec.spoken,
        "prlevel": rec.prlevel,
        "rwp": rec.rwp,
        "syllables_raw": rec.syllables_raw,
        "audio": rec.audio,
        "rime_key": rec.rime_key,
        "graphemes": [g.model_dump() for g in rec.graphemes],
        "phonemes": [p.model_dump() for p in rec.phonemes],
        "chunks": [c.model_dump() for c in rec.chunks],
        "patterns": rec.patterns,
    }


def ingest_words(db: Neo4jDB, records: list[WordRecord], batch_size: int = 500) -> dict[str, int]:
    """Load parsed word records into the graph in idempotent batches.

    Args:
        db: An open :class:`Neo4jDB`.
        records: Parsed word records to load.
        batch_size: Number of words per write transaction.

    Returns:
        Summary counts (``words`` ingested, ``minimal_pairs`` created).
    """
    total = 0
    for batch in chunked(records, batch_size):
        payload = [_record_to_payload(r) for r in batch]
        for stmt in _STATEMENTS:
            db.write_batches(stmt, payload)
        total += len(batch)
        log.info("ingest.batch", loaded=total, of=len(records))

    pairs = derive_minimal_pairs(records)
    for batch in chunked(pairs, 1000):
        db.write_batches(_MINIMAL_PAIRS, batch)
    log.info("ingest.done", words=total, minimal_pairs=len(pairs))
    return {"words": total, "minimal_pairs": len(pairs)}


def derive_minimal_pairs(
    records: list[WordRecord], max_bucket: int = 25, cap: int | None = None
) -> list[dict[str, str]]:
    """Find minimal pairs (differ by exactly one phoneme) via wildcard hashing.

    For each word of phoneme length L we emit L wildcard keys (one position
    blanked). Words sharing a key are minimal pairs. This is O(n·L) rather than
    the O(n²) of pairwise comparison.

    Args:
        records: Parsed words (need non-empty phoneme sequences).
        max_bucket: Skip buckets larger than this to avoid edge explosion on
            very common patterns (e.g. the ``_at`` family).
        cap: Optional hard cap on the number of pairs returned.

    Returns:
        List of ``{"a": word, "b": word}`` dicts (each unordered pair once).
    """
    buckets: dict[tuple, list[str]] = collections.defaultdict(list)
    for rec in records:
        seq = rec.phoneme_seq
        if len(seq) < 2:
            continue
        for i in range(len(seq)):
            key = (len(seq), i, seq[:i], seq[i + 1 :])
            buckets[key].append(rec.text)

    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, str]] = []
    for words in buckets.values():
        if len(words) < 2 or len(words) > max_bucket:
            continue
        for i in range(len(words)):
            for j in range(i + 1, len(words)):
                a, b = words[i], words[j]
                if a == b:
                    continue
                ordered = (a, b) if a < b else (b, a)
                if ordered in seen:
                    continue
                seen.add(ordered)
                pairs.append({"a": ordered[0], "b": ordered[1]})
                if cap and len(pairs) >= cap:
                    return pairs
    return pairs


def load_from_dir(
    db: Neo4jDB, words_dir: str | Path, limit: int | None = None, batch_size: int = 500
) -> dict[str, int]:
    """Parse word files under ``words_dir`` and ingest them.

    Returns ingestion summary plus ``parsed``/``skipped`` file counts.
    """
    paths = iter_word_files(words_dir, limit)
    records: list[WordRecord] = []
    skipped = 0
    for p in paths:
        rec = parse_word_file(p)
        if rec is None:
            skipped += 1
            continue
        records.append(rec)
    log.info("parse.done", parsed=len(records), skipped=skipped, files=len(paths))
    summary = ingest_words(db, records, batch_size=batch_size)
    summary.update({"parsed": len(records), "skipped": skipped})
    return summary
