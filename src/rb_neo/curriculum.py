"""The phonics curriculum as a prerequisite skill DAG in the graph.

This is the layer that makes the ZPD computable (docs/research.md §Layer 1-2):
a hand-authored scope & sequence (UK Letters-and-Sounds style) where each
``Skill`` node is one grapheme-phoneme correspondence and ``PREREQUISITE_OF``
edges encode what must be mastered first. Composite skills require their
component letters (you can't learn ``sh`` before ``s`` and ``h``); single
letters carry only a teaching order (``seq``).

A learner's **zone of proximal development** is then a pure graph query:
unmastered skills whose prerequisites are all mastered — not too easy, not too
hard, learnable right now.

Skills are keyed by lowercase grapheme text (``key``); ``apply_curriculum``
also stamps ``Grapheme.key = toLower(text)`` so the corpus's case-variant
grapheme nodes (``Ss``, ``Ll``, ``A``…) collapse to one skill each.
"""

from __future__ import annotations

from .db import Neo4jDB
from .logging import get_logger

log = get_logger()

# Teaching groups in scope-and-sequence order (phase, skills). Composite skills
# appear after every component letter they require.
PHASES: list[tuple[int, list[str]]] = [
    (2, ["s", "a", "t", "p"]),
    (2, ["i", "n", "m", "d"]),
    (2, ["g", "o", "c", "k"]),
    (2, ["ck", "e", "u", "r"]),
    (2, ["h", "b", "f", "ff"]),
    (2, ["l", "ll", "ss"]),
    (3, ["j", "v", "w", "x"]),
    (3, ["y", "z", "zz"]),
    (3, ["ch", "sh", "th"]),
    (3, ["ai", "ee", "oa", "oo"]),
    (3, ["ar", "or", "er", "ir", "ur"]),
]

# Hard prerequisites: a composite grapheme requires its component letters.
REQUIRES: dict[str, list[str]] = {
    "ck": ["c", "k"],
    "ff": ["f"],
    "ll": ["l"],
    "ss": ["s"],
    "zz": ["z"],
    "ch": ["c", "h"],
    "sh": ["s", "h"],
    "th": ["t", "h"],
    "ai": ["a", "i"],
    "ee": ["e"],
    "oa": ["o", "a"],
    "oo": ["o"],
    "ar": ["a", "r"],
    "or": ["o", "r"],
    "er": ["e", "r"],
    "ir": ["i", "r"],
    "ur": ["u", "r"],
}

_DOUBLES = {"ff", "ll", "ss", "zz"}
_DIGRAPHS = {"ch", "sh", "th", "ck"}
_R_CONTROLLED = {"ar", "or", "er", "ir", "ur"}
_VOWEL_TEAMS = {"ai", "ee", "oa", "oo"}


def kind_of(key: str) -> str:
    """Classify a skill key into its pedagogical kind."""
    if key in _DOUBLES:
        return "double"
    if key in _DIGRAPHS:
        return "digraph"
    if key in _R_CONTROLLED:
        return "r_controlled"
    if key in _VOWEL_TEAMS:
        return "vowel_team"
    return "letter"


#: Flat teaching order — index is the ``seq`` property on each Skill node.
SEQUENCE: list[str] = [key for _, group in PHASES for key in group]


def skill_rows() -> list[dict]:
    """All skills as Cypher-ready parameter rows."""
    rows = []
    for phase, group in PHASES:
        for key in group:
            rows.append(
                {"key": key, "kind": kind_of(key), "phase": phase, "seq": SEQUENCE.index(key)}
            )
    return rows


def prerequisite_rows() -> list[dict]:
    """All PREREQUISITE_OF edges as Cypher-ready parameter rows."""
    return [{"prereq": p, "skill": s} for s, prereqs in REQUIRES.items() for p in prereqs]


_SET_GRAPHEME_KEYS = "MATCH (g:Grapheme) SET g.key = toLower(g.text)"

_MERGE_SKILLS = """
UNWIND $batch AS s
MERGE (sk:Skill {key: s.key})
SET sk.kind = s.kind, sk.phase = s.phase, sk.seq = s.seq
"""

_MERGE_PREREQS = """
UNWIND $batch AS e
MATCH (a:Skill {key: e.prereq}), (b:Skill {key: e.skill})
MERGE (a)-[:PREREQUISITE_OF]->(b)
"""

# Connect every grapheme (and its case variants) to the curriculum skill it
# teaches, so ZPD traversals are pure graph hops instead of key-property matches.
_LINK_GRAPHEME_SKILLS = """
MATCH (g:Grapheme), (s:Skill {key: g.key})
MERGE (g)-[:IS_SKILL]->(s)
"""


def apply_curriculum(db: Neo4jDB) -> dict[str, int]:
    """Write the skill DAG into the graph (idempotent) and key the graphemes.

    Returns:
        Counts of skills and prerequisite edges merged.
    """
    db.write(_SET_GRAPHEME_KEYS)
    skills = skill_rows()
    prereqs = prerequisite_rows()
    db.write_batches(_MERGE_SKILLS, skills)
    db.write_batches(_MERGE_PREREQS, prereqs)
    db.write(_LINK_GRAPHEME_SKILLS)
    log.info("curriculum.applied", skills=len(skills), prerequisites=len(prereqs))
    return {"skills": len(skills), "prerequisites": len(prereqs)}
