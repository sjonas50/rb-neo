"""Generate synthetic learners with mastery + attempt history.

No real child data is ever used (see compliance note in ``docs/research.md``).
Learners follow a synthetic-phonics scope & sequence: each profile "knows" a
prefix of the sequence and is mid-learning the next few graphemes. Mastery is
not asserted directly — it *emerges* from generated ``ATTEMPTED`` edges run
through the BKT model, so the demo exercises the real pipeline.
"""

from __future__ import annotations

from .db import Neo4jDB
from .logging import get_logger
from .mastery import update_from_attempts
from .models import Learner

log = get_logger()

# A synthetic-phonics order (Letters-and-Sounds style): single letters, then
# consonant digraphs, then r-controlled / vowel teams.
SEQUENCE: list[str] = [
    "s",
    "a",
    "t",
    "p",
    "i",
    "n",
    "m",
    "d",
    "g",
    "o",
    "c",
    "k",
    "e",
    "u",
    "r",
    "h",
    "b",
    "f",
    "l",
    "j",
    "v",
    "w",
    "x",
    "y",
    "z",
    "q",
    "ch",
    "sh",
    "th",
    "ck",
    "ng",
    "ar",
    "or",
    "er",
    "ai",
    "ee",
    "oa",
    "oo",
]

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


# Personas give the LLM something to personalize around. Ben and Maya share the
# SAME mastery (known=27) on purpose: that makes the "same skill, two kids,
# identical safe set, different lesson" split-screen airtight.
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
        known=11,
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
        known=27,
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
        known=27,
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
        known=32,
    ),
]

_MERGE_LEARNER = """
MERGE (l:Learner {id: $id})
SET l.name = $name, l.level = $level,
    l.age = $age, l.emoji = $emoji, l.interests = $interests
"""

_WORDS_FOR_GRAPHEME = """
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme {text: $gt})
RETURN w.text AS word ORDER BY size(w.text), w.text LIMIT $k
"""

_WRITE_ATTEMPTS = """
UNWIND $rows AS row
MATCH (l:Learner {id: $id}), (w:Word {text: row.word})
MERGE (l)-[a:ATTEMPTED {ts: row.ts}]->(w)
SET a.correct = row.correct
"""

_CLEAR_LEARNERS = "MATCH (l:Learner) DETACH DELETE l"


def _words_for(db: Neo4jDB, grapheme: str, k: int) -> list[str]:
    return [r["word"] for r in db.query(_WORDS_FOR_GRAPHEME, gt=grapheme, k=k)]


def seed_learners(db: Neo4jDB, reps_known: int = 4, reps_frontier: int = 2) -> list[Learner]:
    """Create synthetic learners with attempt history and computed mastery.

    Args:
        db: Open database (the content graph must already be ingested).
        reps_known: Correct attempts generated per mastered grapheme.
        reps_frontier: Attempts per frontier grapheme (alternating correct/incorrect).

    Returns:
        The learners created.
    """
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

        # Frontier first (earlier in time): in-progress, ~half correct.
        for g in profile.frontier_graphemes:
            for w in _words_for(db, g, reps_frontier):
                for r in range(reps_frontier):
                    rows.append({"word": w, "correct": (r % 2 == 0), "ts": ts})
                    ts += 3600

        # Mastered practice later (more recent): consistently correct -> crosses threshold.
        for g in profile.mastered_graphemes:
            for w in _words_for(db, g, 3):
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
