"""Generate synthetic learners with mastery + attempt history.

No real child data is ever used (see compliance note in ``docs/research.md``).
Learners follow a synthetic-phonics scope & sequence: each profile "knows" a
prefix of the sequence and is mid-learning the next few graphemes. Mastery is
not asserted directly — it *emerges* from generated ``ATTEMPTED`` edges run
through the BKT model, so the demo exercises the real pipeline.
"""

from __future__ import annotations

from .curriculum import SEQUENCE, apply_curriculum
from .db import Neo4jDB
from .logging import get_logger
from .mastery import update_from_attempts
from .models import Learner

log = get_logger()

# Deterministic base timestamp (2026-01-01T00:00:00Z) — avoids wall-clock so the
# synthetic graph is reproducible.
_BASE_TS = 1_767_225_600


class Profile:
    """A synthetic learner profile: how far along the sequence, and frontier width."""

    def __init__(self, learner: Learner, known: int, frontier: int = 3) -> None:
        self.learner = learner
        self.known = known
        self.frontier = frontier

    @property
    def mastered_graphemes(self) -> list[str]:
        return SEQUENCE[: self.known]

    @property
    def frontier_graphemes(self) -> list[str]:
        return SEQUENCE[self.known : self.known + self.frontier]


# Personas give the LLM something to personalize around, and each sits at a
# pedagogically distinct point on the curriculum DAG:
#   Ava  (known=12, through 'k')  — her ZPD just unlocked 'ck' (c+k both fresh);
#                                   digraphs like 'sh' stay visibly locked (no 'h').
#   Ben/Maya (known=31, through 'ch') — SAME mastery on purpose: that makes the
#                                   "same skill, two kids, identical safe set,
#                                   different lesson" split-screen airtight.
#   Cara (known=37, through 'oo') — frontier is the r-controlled vowels.
PROFILES: list[Profile] = [
    Profile(
        Learner(
            id="ava",
            name="Ava",
            level="beginner",
            age=5,
            emoji="🦕",
            interests=["dinosaurs", "digging", "the color green"],
        ),
        known=12,
    ),
    Profile(
        Learner(
            id="ben",
            name="Ben",
            level="mid",
            age=6,
            emoji="⚽",
            interests=["soccer", "his dog Rex", "pizza"],
        ),
        known=31,
    ),
    Profile(
        Learner(
            id="maya",
            name="Maya",
            level="mid",
            age=6,
            emoji="🚀",
            interests=["space", "rockets", "the moon"],
        ),
        known=31,
    ),
    Profile(
        Learner(
            id="cara",
            name="Cara",
            level="advanced",
            age=7,
            emoji="🎨",
            interests=["painting", "horses", "rainbows"],
        ),
        known=37,
    ),
]

_MERGE_LEARNER = """
MERGE (l:Learner {id: $id})
SET l.name = $name, l.level = $level,
    l.age = $age, l.emoji = $emoji, l.interests = $interests
"""

# Practice words for a grapheme, constrained to the learner's taught scope:
# every grapheme key in the word must be in $allowed. Without this constraint,
# practicing 's' with a word like 'sauce' would leak BKT credit for 'au'/'ce'
# the persona was never taught — and the personas stop matching their stories.
_WORDS_FOR_GRAPHEME = """
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE g.key = $gt AND w.text =~ '[a-z]{2,}'
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
    WHERE NOT o.key IN $allowed
  }
RETURN DISTINCT w.text AS word ORDER BY size(w.text), w.text LIMIT $k
"""

_WRITE_ATTEMPTS = """
UNWIND $rows AS row
MATCH (l:Learner {id: $id}), (w:Word {text: row.word})
MERGE (l)-[a:ATTEMPTED {ts: row.ts}]->(w)
SET a.correct = row.correct
"""

_CLEAR_LEARNERS = "MATCH (l:Learner) DETACH DELETE l"


def _words_for(db: Neo4jDB, grapheme: str, allowed: list[str], k: int) -> list[str]:
    return [r["word"] for r in db.query(_WORDS_FOR_GRAPHEME, gt=grapheme, allowed=allowed, k=k)]


def seed_learners(db: Neo4jDB, reps_known: int = 4, reps_frontier: int = 2) -> list[Learner]:
    """Create synthetic learners with attempt history and computed mastery.

    Also (re)applies the curriculum skill DAG so grapheme keys and Skill nodes
    exist before mastery edges are written.

    Args:
        db: Open database (the content graph must already be ingested).
        reps_known: Correct attempts generated per mastered grapheme.
        reps_frontier: Attempts per frontier grapheme (alternating correct/incorrect).

    Returns:
        The learners created.
    """
    apply_curriculum(db)
    db.write(_CLEAR_LEARNERS)
    created: list[Learner] = []

    for profile in PROFILES:
        learner = profile.learner
        db.write(
            _MERGE_LEARNER,
            id=learner.id,
            name=learner.name,
            level=learner.level,
            age=learner.age,
            emoji=learner.emoji,
            interests=learner.interests,
        )

        rows: list[dict] = []
        ts = _BASE_TS
        known = profile.mastered_graphemes

        # Frontier first (earlier in time): in-progress, ~half correct. Practice
        # words may use the known prefix plus the one frontier grapheme.
        for g in profile.frontier_graphemes:
            for w in _words_for(db, g, allowed=[*known, g], k=reps_frontier):
                for r in range(reps_frontier):
                    rows.append({"word": w, "correct": (r % 2 == 0), "ts": ts})
                    ts += 3600

        # Mastered practice later (more recent): consistently correct -> crosses
        # threshold. Words stay fully inside the taught scope.
        for g in known:
            for w in _words_for(db, g, allowed=known, k=3):
                for _ in range(reps_known):
                    rows.append({"word": w, "correct": True, "ts": ts})
                    ts += 3600

        db.write(_WRITE_ATTEMPTS, id=learner.id, rows=rows)
        estimates = update_from_attempts(db, learner.id)
        mastered = sum(1 for e in estimates if e.p >= 0.85)
        log.info(
            "synthetic.learner",
            id=learner.id,
            attempts=len(rows),
            skills=len(estimates),
            mastered=mastered,
        )
        created.append(learner)

    return created
