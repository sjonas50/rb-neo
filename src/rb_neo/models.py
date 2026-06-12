"""Pydantic models for parsed word records and graph payloads."""

from __future__ import annotations

from pydantic import BaseModel, Field


class GraphemeUnit(BaseModel):
    """A level-A grapheme (letter / digraph / blend) with its paired sound."""

    pos: int
    text: str
    type: str  # letter | digraph | blend | r_controlled | vowel_team | morpheme
    length: int
    sound: str = ""  # mp3 id, or "" when absent


class ChunkUnit(BaseModel):
    """A level-B chunk (onset/rime) or level-C syllable."""

    pos: int
    text: str
    level: str  # "B" or "C"
    sound: str = ""


class PhonemeUnit(BaseModel):
    """A single ARPABET-style phoneme from sgstRWP."""

    pos: int
    text: str
    is_vowel: bool


class WordRecord(BaseModel):
    """Fully parsed representation of one ``<word>.json`` file."""

    text: str
    written: str
    spoken: str
    prlevel: str = ""
    rwp: str = ""
    syllables_raw: str = ""
    audio: str = ""
    graphemes: list[GraphemeUnit] = Field(default_factory=list)
    chunks: list[ChunkUnit] = Field(default_factory=list)  # level B and C
    phonemes: list[PhonemeUnit] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    rime_key: str = ""

    @property
    def phoneme_seq(self) -> tuple[str, ...]:
        """Phoneme texts as a tuple (used for minimal-pair hashing)."""
        return tuple(p.text for p in self.phonemes)


# -- learner model (Phase 2) -----------------------------------------------------


class BKTParams(BaseModel):
    """Bayesian Knowledge Tracing parameters for one skill.

    Attributes:
        p_l0: Prior probability the skill is already known.
        p_t: Probability of transitioning unknown -> known per opportunity.
        p_s: Slip — probability of an error when the skill is known.
        p_g: Guess — probability of a correct answer when the skill is unknown.
    """

    p_l0: float = 0.2
    p_t: float = 0.3
    p_s: float = 0.1
    p_g: float = 0.2


class Attempt(BaseModel):
    """A learner's attempt at reading a word."""

    word: str
    correct: bool
    ts: int  # epoch seconds (deterministic in synthetic data)


class MasteryEstimate(BaseModel):
    """Posterior mastery for one skill after processing a learner's attempts."""

    skill: str
    skill_kind: str  # "grapheme" | "phoneme"
    p: float
    attempts: int


class Learner(BaseModel):
    """A (synthetic) learner profile."""

    id: str
    name: str
    level: str  # e.g. "beginner" | "mid" | "advanced"
