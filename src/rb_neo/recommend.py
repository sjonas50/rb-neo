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

# "Presentable" words: real, classroom-friendly tokens (lowercase a-z, 3+ letters).
# Inlined into the candidate queries to filter out the corpus's fragment /
# abbreviation entries for audience-facing use.

# A word is at the learner's i+1 frontier when every grapheme is mastered EXCEPT
# exactly one new target — maximal reuse of known units, one new thing to learn.
# Graphemes compare by lowercase `key` so case variants ('Ss'/'ss') are one skill.
_NEXT_BEST_WORD = """
MATCH (l:Learner {id: $learner_id})
OPTIONAL MATCH (l)-[:MASTERED {mastered: true}]->(mg:Grapheme)
WITH collect(DISTINCT mg.key) AS known
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE ($level IS NULL OR w.prlevel = $level)
  AND w.text =~ '[a-z]{3,}'
  AND (NOT $common_only OR w.common = true)
WITH w, known, collect(DISTINCT g.key) AS keys
WITH w, keys, [k IN keys WHERE NOT k IN known] AS new
WHERE size(new) = 1
OPTIONAL MATCH (s:Skill {key: new[0]})
RETURN w.text AS word,
       new[0] AS introduces,
       coalesce(s.kind, 'grapheme') AS introduces_type,
       size(keys) AS units
ORDER BY units ASC, word ASC
LIMIT $limit
"""

# Words that practice a specific target grapheme while every OTHER grapheme is
# already mastered — ideal decodable practice to teach that one skill.
_CROSS_WORD = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)-[:HAS_GRAPHEME]->(t:Grapheme)
WHERE t.key = $target
  AND w.text =~ '[a-z]{3,}'
  AND (NOT $common_only OR w.common = true)
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
    WHERE o.key <> $target AND NOT (l)-[:MASTERED {mastered: true}]->(o)
  }
RETURN DISTINCT w.text AS word, size([(w)-[:HAS_GRAPHEME]->(x) | x]) AS units
ORDER BY units ASC, word ASC
LIMIT $limit
"""

# Fully decodable words: every grapheme already mastered. Fluency / confidence practice.
_MASTERY_AWARE = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)
WHERE w.text =~ '[a-z]{3,}'
  AND (NOT $common_only OR w.common = true)
  AND NOT EXISTS {
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
MATCH (s:Skill)
WITH count(s) AS total
MATCH (l:Learner {id: $learner_id})
OPTIONAL MATCH (l)-[m:MASTERED {mastered: true}]->(:Skill)
RETURN l.name AS name, l.level AS level,
       total AS skills,
       count(m) AS mastered
"""

# ── ZPD over the curriculum DAG ──────────────────────────────────────────────
# The learner's zone of proximal development: skills NOT yet mastered whose
# prerequisites are ALL mastered — learnable right now. Each is scored by
# leverage: how many curated words become decodable the moment it is learned.
_ZPD_POOL = """
MATCH (l:Learner {id: $learner_id})
MATCH (s:Skill)
WHERE NOT (l)-[:MASTERED {mastered: true}]->(s)
  AND NOT EXISTS {
    MATCH (p:Skill)-[:PREREQUISITE_OF]->(s)
    WHERE NOT (l)-[:MASTERED {mastered: true}]->(p)
  }
WITH l, s, COUNT {
  MATCH (g:Grapheme {key: s.key})<-[:HAS_GRAPHEME]-(w:Word)
  WHERE w.common = true AND w.text =~ '[a-z]{3,}'
    AND NOT EXISTS {
      MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
      WHERE o.key <> s.key AND NOT (l)-[:MASTERED {mastered: true}]->(o)
    }
  RETURN DISTINCT w
} AS unlocks
RETURN s.key AS skill, s.kind AS kind, s.phase AS phase, s.seq AS seq, unlocks
ORDER BY unlocks DESC, seq ASC
LIMIT $limit
"""

# Skills beyond the ZPD: blocked because at least one prerequisite is unmastered.
_LOCKED_SKILLS = """
MATCH (l:Learner {id: $learner_id})
MATCH (p:Skill)-[:PREREQUISITE_OF]->(s:Skill)
WHERE NOT (l)-[:MASTERED {mastered: true}]->(s)
  AND NOT (l)-[:MASTERED {mastered: true}]->(p)
RETURN s.key AS skill, s.kind AS kind, s.seq AS seq,
       collect(p.key) AS missing
ORDER BY seq ASC
"""

# Every skill with its ZPD status for one learner — feeds the skill-map viz.
_SKILL_MAP = """
MATCH (l:Learner {id: $learner_id})
MATCH (s:Skill)
OPTIONAL MATCH (l)-[m:MASTERED]->(s)
WITH s, m,
     [(p:Skill)-[:PREREQUISITE_OF]->(s)
      WHERE NOT (l)-[:MASTERED {mastered: true}]->(p) | p.key] AS missing
RETURN s.key AS skill, s.kind AS kind, s.phase AS phase, s.seq AS seq,
       CASE
         WHEN m IS NOT NULL AND m.mastered THEN 'mastered'
         WHEN size(missing) = 0 THEN 'zpd'
         ELSE 'locked'
       END AS status,
       missing,
       coalesce(m.p, 0.0) AS p
ORDER BY seq ASC
"""

_SKILL_EDGES = """
MATCH (p:Skill)-[:PREREQUISITE_OF]->(s:Skill)
RETURN p.key AS prereq, s.key AS skill
"""

_LIST_LEARNERS = """
MATCH (l:Learner)
RETURN l.id AS id, l.name AS name, l.level AS level,
       l.age AS age, l.emoji AS emoji, l.interests AS interests
ORDER BY l.level, l.name
"""

_GET_LEARNER = """
MATCH (l:Learner {id: $learner_id})
RETURN l.id AS id, l.name AS name, l.level AS level,
       l.age AS age, l.emoji AS emoji, l.interests AS interests
"""


def list_learners(db: Neo4jDB) -> list[dict]:
    """All learners with their personas (for dashboards / dropdowns)."""
    return db.query(_LIST_LEARNERS)


def get_learner(db: Neo4jDB, learner_id: str) -> dict:
    """One learner's persona, or ``{}`` if not found."""
    rows = db.query(_GET_LEARNER, learner_id=learner_id)
    return rows[0] if rows else {}


def next_best_word(
    db: Neo4jDB,
    learner_id: str,
    level: str | None = None,
    limit: int = 15,
    common_only: bool = True,
) -> list[dict]:
    """Words at the learner's i+1 frontier (introduce exactly one new grapheme).

    ``common_only`` restricts to the curated decodable word list (recommended for
    audience-facing use); set False to range over the full corpus.
    """
    return db.query(
        _NEXT_BEST_WORD,
        learner_id=learner_id,
        level=level,
        limit=limit,
        common_only=common_only,
    )


def cross_word(
    db: Neo4jDB, learner_id: str, target: str, limit: int = 15, common_only: bool = True
) -> list[dict]:
    """Decodable words to teach ``target`` (every other grapheme already mastered)."""
    return db.query(
        _CROSS_WORD, learner_id=learner_id, target=target, limit=limit, common_only=common_only
    )


def mastery_aware(
    db: Neo4jDB, learner_id: str, limit: int = 15, common_only: bool = True
) -> list[dict]:
    """Fully decodable words for fluency practice."""
    return db.query(_MASTERY_AWARE, learner_id=learner_id, limit=limit, common_only=common_only)


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
    """Return ``{name, level, skills, mastered}`` over the curriculum skills."""
    rows = db.query(_MASTERY_SUMMARY, learner_id=learner_id)
    return rows[0] if rows else {}


def zpd_pool(db: Neo4jDB, learner_id: str, limit: int = 10) -> list[dict]:
    """The learner's ZPD: unmastered skills with all prerequisites mastered.

    Ranked by leverage — how many curated words each skill would unlock.
    """
    return db.query(_ZPD_POOL, learner_id=learner_id, limit=limit)


def locked_skills(db: Neo4jDB, learner_id: str) -> list[dict]:
    """Skills beyond the ZPD, with the unmastered prerequisites blocking each."""
    return db.query(_LOCKED_SKILLS, learner_id=learner_id)


def skill_map(db: Neo4jDB, learner_id: str) -> list[dict]:
    """Every curriculum skill with status ``mastered`` | ``zpd`` | ``locked``."""
    return db.query(_SKILL_MAP, learner_id=learner_id)


def skill_edges(db: Neo4jDB) -> list[dict]:
    """All PREREQUISITE_OF edges (for rendering the curriculum DAG)."""
    return db.query(_SKILL_EDGES)
