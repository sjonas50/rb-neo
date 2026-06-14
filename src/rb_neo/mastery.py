"""Bayesian Knowledge Tracing (BKT) mastery model over skill nodes.

This is the classic 4-parameter BKT forward update (Corbett & Anderson, 1994),
implemented directly to keep the PoC dependency-light. The algorithm is identical
to what ``pyBKT`` *fits*; in production, swap this module's hand-set parameters
for pyBKT-estimated ones (see ``docs/research.md`` §Layer 2).

Skills tracked are **graphemes** (the decoding unit a child must master). An
attempt on a word is treated as a multi-skill observation: the correct/incorrect
signal is applied to every grapheme in that word.
"""

from __future__ import annotations

import math

from .db import Neo4jDB
from .logging import get_logger
from .models import Attempt, BKTParams, MasteryEstimate

log = get_logger()

DEFAULT_THRESHOLD = 0.85

# Per-type priors: harder grapheme types start lower and are guessed less often.
_PARAMS_BY_TYPE: dict[str, BKTParams] = {
    "letter": BKTParams(p_l0=0.30, p_t=0.30, p_s=0.10, p_g=0.20),
    "digraph": BKTParams(p_l0=0.15, p_t=0.25, p_s=0.12, p_g=0.12),
    "blend": BKTParams(p_l0=0.12, p_t=0.22, p_s=0.12, p_g=0.10),
    "r_controlled": BKTParams(p_l0=0.12, p_t=0.22, p_s=0.12, p_g=0.10),
    "vowel_team": BKTParams(p_l0=0.10, p_t=0.20, p_s=0.14, p_g=0.10),
    "morpheme": BKTParams(p_l0=0.10, p_t=0.20, p_s=0.14, p_g=0.10),
}
_DEFAULT_PARAMS = BKTParams()


def params_for(grapheme_type: str) -> BKTParams:
    """Return BKT parameters for a grapheme type."""
    return _PARAMS_BY_TYPE.get(grapheme_type, _DEFAULT_PARAMS)


def bkt_update(p_known: float, correct: bool, params: BKTParams) -> float:
    """Apply one BKT observation and return the updated P(known).

    Args:
        p_known: Current probability the skill is known.
        correct: Whether the observed attempt was correct.
        params: BKT parameters for the skill.

    Returns:
        Updated probability the skill is known, after the evidence and the
        learning (transit) step.
    """
    if correct:
        num = p_known * (1 - params.p_s)
        den = num + (1 - p_known) * params.p_g
    else:
        num = p_known * params.p_s
        den = num + (1 - p_known) * (1 - params.p_g)
    posterior = num / den if den > 0 else p_known
    # Learning step: even after the observation, the skill may transition known.
    return posterior + (1 - posterior) * params.p_t


def apply_decay(p_known: float, days_idle: float, rate: float = 0.08) -> float:
    """Exponentially decay mastery toward 0.5 (uncertainty) over idle days.

    Forgetting pulls a confident estimate back toward "unsure" rather than to 0,
    which matches how unpracticed skills erode without becoming actively wrong.
    """
    if days_idle <= 0:
        return p_known
    factor = math.exp(-rate * days_idle)
    return 0.5 + (p_known - 0.5) * factor


def is_mastered(p_known: float, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """Return True if the mastery probability meets the threshold."""
    return p_known >= threshold


def compute_mastery(
    attempts: list[Attempt],
    word_graphemes: dict[str, list[tuple[str, str]]],
    decay_rate: float = 0.08,
) -> list[MasteryEstimate]:
    """Run BKT over a learner's attempts and return per-grapheme mastery.

    Args:
        attempts: The learner's attempts, any order (sorted internally by ts).
        word_graphemes: Map of word text -> list of ``(grapheme_text, type)``.
        decay_rate: Forgetting rate per idle day between consecutive attempts on
            the same skill.

    Returns:
        One :class:`MasteryEstimate` per grapheme the learner has encountered.
    """
    ordered = sorted(attempts, key=lambda a: a.ts)
    p_state: dict[str, float] = {}
    g_type: dict[str, str] = {}
    last_ts: dict[str, int] = {}
    counts: dict[str, int] = {}

    for att in ordered:
        for gtext, gtype in word_graphemes.get(att.word, []):
            # Skill identity is the lowercase key: the corpus's case-variant
            # grapheme nodes ('s', 'Ss'→'ss', 'Ll'→'ll') are the same skill.
            key = gtext.lower()
            g_type[key] = gtype
            params = params_for(gtype)
            if key not in p_state:
                p_state[key] = params.p_l0
                counts[key] = 0
            else:
                days_idle = (att.ts - last_ts[key]) / 86400.0
                p_state[key] = apply_decay(p_state[key], days_idle, decay_rate)
            p_state[key] = bkt_update(p_state[key], att.correct, params)
            last_ts[key] = att.ts
            counts[key] += 1

    return [
        MasteryEstimate(skill=g, skill_kind="grapheme", p=round(p, 4), attempts=counts[g])
        for g, p in sorted(p_state.items())
    ]


# -- graph I/O -------------------------------------------------------------------

_READ_ATTEMPTS = """
MATCH (l:Learner {id: $learner_id})-[a:ATTEMPTED]->(w:Word)
RETURN w.text AS word, a.correct AS correct, a.ts AS ts
ORDER BY a.ts
"""

_READ_WORD_GRAPHEMES = """
UNWIND $words AS wt
MATCH (w:Word {text: wt})-[r:HAS_GRAPHEME]->(g:Grapheme)
RETURN wt AS word, g.text AS gtext, g.type AS gtype
ORDER BY r.pos
"""

# Mastery lands on every case-variant grapheme node sharing the key (so the
# word-decodability edge checks are case-proof) AND on the curriculum Skill
# node (so the ZPD prerequisite traversal is one hop).
_WRITE_MASTERY = """
UNWIND $rows AS row
MATCH (l:Learner {id: $learner_id})
MATCH (g:Grapheme {key: row.skill})
MERGE (l)-[m:MASTERED]->(g)
SET m.p = row.p, m.attempts = row.attempts, m.mastered = row.mastered
"""

_WRITE_SKILL_MASTERY = """
UNWIND $rows AS row
MATCH (l:Learner {id: $learner_id})
MATCH (s:Skill {key: row.skill})
MERGE (l)-[m:MASTERED]->(s)
SET m.p = row.p, m.attempts = row.attempts, m.mastered = row.mastered
"""


def update_from_attempts(
    db: Neo4jDB, learner_id: str, threshold: float = DEFAULT_THRESHOLD
) -> list[MasteryEstimate]:
    """Read a learner's attempts from the graph, run BKT, write MASTERED edges.

    The ``MASTERED`` edge carries ``p`` (posterior), ``attempts`` and a boolean
    ``mastered`` flag, so downstream recommender queries can filter cheaply.
    """
    attempts = [
        Attempt(word=r["word"], correct=bool(r["correct"]), ts=int(r["ts"]))
        for r in db.query(_READ_ATTEMPTS, learner_id=learner_id)
    ]
    words = sorted({a.word for a in attempts})
    word_graphemes: dict[str, list[tuple[str, str]]] = {}
    for r in db.query(_READ_WORD_GRAPHEMES, words=words):
        word_graphemes.setdefault(r["word"], []).append((r["gtext"], r["gtype"]))

    estimates = compute_mastery(attempts, word_graphemes)
    rows = [
        {
            "skill": e.skill,
            "p": e.p,
            "attempts": e.attempts,
            "mastered": is_mastered(e.p, threshold),
        }
        for e in estimates
    ]
    db.write(_WRITE_MASTERY, learner_id=learner_id, rows=rows)
    db.write(_WRITE_SKILL_MASTERY, learner_id=learner_id, rows=rows)
    log.info("mastery.updated", learner=learner_id, skills=len(rows))
    return estimates
