"""ZPD recommender: the graph queries that personalize reading.

Each function is a single Cypher traversal over the content graph + a learner's
mastery overlay. This is the core argument for using a graph: "next-best word",
"cross-word reinforcement" and "fully decodable set" are one query each — not
multi-table joins plus app-side filtering.

Mastery is read from ``MASTERED {mastered:true}`` edges written by
:mod:`rb_neo.mastery`.
"""

from __future__ import annotations

from .db import Neo4jDB

# A word is at the learner's i+1 frontier when every grapheme is mastered EXCEPT
# exactly one new target — maximal reuse of known units, one new thing to learn.
_NEXT_BEST_WORD = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE $level IS NULL OR w.prlevel = $level
WITH l, w, collect(DISTINCT g) AS gs
WITH l, w, gs,
     [x IN gs WHERE NOT (l)-[:MASTERED {mastered: true}]->(x)] AS unmastered
WHERE size(unmastered) = 1
RETURN w.text AS word,
       unmastered[0].text AS introduces,
       unmastered[0].type AS introduces_type,
       size(gs) AS units
ORDER BY units ASC, word ASC
LIMIT $limit
"""

# Words that practice a specific target grapheme while every OTHER grapheme is
# already mastered — ideal decodable practice to teach that one skill.
_CROSS_WORD = """
MATCH (l:Learner {id: $learner_id}), (t:Grapheme {text: $target})
MATCH (w:Word)-[:HAS_GRAPHEME]->(t)
WHERE NOT EXISTS {
  MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
  WHERE o.text <> $target AND NOT (l)-[:MASTERED {mastered: true}]->(o)
}
RETURN w.text AS word, size([(w)-[:HAS_GRAPHEME]->(x) | x]) AS units
ORDER BY units ASC, word ASC
LIMIT $limit
"""

# Fully decodable words: every grapheme already mastered. Fluency / confidence practice.
_MASTERY_AWARE = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)
WHERE NOT EXISTS {
  MATCH (w)-[:HAS_GRAPHEME]->(g:Grapheme)
  WHERE NOT (l)-[:MASTERED {mastered: true}]->(g)
}
RETURN w.text AS word, w.prlevel AS level, size([(w)-[:HAS_GRAPHEME]->(x) | x]) AS units
ORDER BY units ASC, word ASC
LIMIT $limit
"""

# Graphemes the learner gets wrong most and has not yet mastered -> target next.
_REMEDIATION = """
MATCH (l:Learner {id: $learner_id})-[:ATTEMPTED {correct: false}]->(w:Word)
MATCH (w)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE NOT (l)-[:MASTERED {mastered: true}]->(g)
RETURN g.text AS grapheme, g.type AS type, count(*) AS misses
ORDER BY misses DESC, grapheme ASC
LIMIT $limit
"""

# Weakest mastered skills (closest to slipping) -> spaced-repetition-lite review.
# Production: replace with FSRS scheduling (see docs/research.md §spaced repetition).
_REVIEW_QUEUE = """
MATCH (l:Learner {id: $learner_id})-[m:MASTERED {mastered: true}]->(g:Grapheme)
RETURN g.text AS grapheme, g.type AS type, m.p AS p, m.attempts AS attempts
ORDER BY m.p ASC, attempts ASC
LIMIT $limit
"""

_RHYME_FAMILY = """
MATCH (w:Word {text: $word})-[:HAS_RIME]->(r:Rime)<-[:HAS_RIME]-(other:Word)
WHERE other.text <> $word
RETURN other.text AS word, r.key AS rime
ORDER BY size(other.text), other.text
LIMIT $limit
"""

_MINIMAL_PAIRS = """
MATCH (w:Word {text: $word})-[:MINIMAL_PAIR_OF]-(o:Word)
RETURN o.text AS word
ORDER BY o.text
LIMIT $limit
"""

_MASTERY_SUMMARY = """
MATCH (l:Learner {id: $learner_id})
OPTIONAL MATCH (l)-[m:MASTERED]->(:Grapheme)
RETURN l.name AS name, l.level AS level,
       count(m) AS skills,
       sum(CASE WHEN m.mastered THEN 1 ELSE 0 END) AS mastered
"""


def next_best_word(
    db: Neo4jDB, learner_id: str, level: str | None = None, limit: int = 15
) -> list[dict]:
    """Words at the learner's i+1 frontier (introduce exactly one new grapheme)."""
    return db.query(_NEXT_BEST_WORD, learner_id=learner_id, level=level, limit=limit)


def cross_word(db: Neo4jDB, learner_id: str, target: str, limit: int = 15) -> list[dict]:
    """Decodable words to teach ``target`` (every other grapheme already mastered)."""
    return db.query(_CROSS_WORD, learner_id=learner_id, target=target, limit=limit)


def mastery_aware(db: Neo4jDB, learner_id: str, limit: int = 15) -> list[dict]:
    """Fully decodable words for fluency practice."""
    return db.query(_MASTERY_AWARE, learner_id=learner_id, limit=limit)


def remediation(db: Neo4jDB, learner_id: str, limit: int = 10) -> list[dict]:
    """Unmastered graphemes the learner misses most often."""
    return db.query(_REMEDIATION, learner_id=learner_id, limit=limit)


def review_queue(db: Neo4jDB, learner_id: str, limit: int = 10) -> list[dict]:
    """Mastered skills closest to slipping (spaced-repetition-lite)."""
    return db.query(_REVIEW_QUEUE, learner_id=learner_id, limit=limit)


def rhyme_family(db: Neo4jDB, word: str, limit: int = 15) -> list[dict]:
    """Words that rhyme with ``word`` (share a Rime node)."""
    return db.query(_RHYME_FAMILY, word=word, limit=limit)


def minimal_pairs(db: Neo4jDB, word: str, limit: int = 15) -> list[dict]:
    """Words differing from ``word`` by exactly one phoneme."""
    return db.query(_MINIMAL_PAIRS, word=word, limit=limit)


def mastery_summary(db: Neo4jDB, learner_id: str) -> dict:
    """Return ``{name, level, skills, mastered}`` for a learner."""
    rows = db.query(_MASTERY_SUMMARY, learner_id=learner_id)
    return rows[0] if rows else {}
