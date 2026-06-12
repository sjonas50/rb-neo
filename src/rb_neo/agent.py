"""Optional LLM layer: turn graph recommendations into teacher-facing guidance.

The graph already decides *what* to teach (see :mod:`rb_neo.recommend`). This
module only *narrates* it — a teacher rationale plus a decodable practice
sentence — using Claude with a forced-tool structured output.

Design choices:
- **The LLM never makes the pedagogical decision.** It receives the graph's
  next-best words + remediation target and explains/packages them. This keeps
  routing deterministic, auditable, and cheap.
- **Graceful degradation.** With no ``ANTHROPIC_API_KEY`` set, a deterministic
  offline recommendation is returned so the demo and tests run without network
  or cost.
- **Compliance.** Only synthetic, non-PII content is ever sent (see
  ``docs/research.md`` §Compliance).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from . import recommend
from .config import Settings, get_settings
from .db import Neo4jDB
from .logging import get_logger

log = get_logger()


class Recommendation(BaseModel):
    """Structured output returned to a teacher/app for one learner."""

    target_skill: str = Field(description="The single new grapheme to introduce next.")
    words: list[str] = Field(description="Decodable practice words for this target.")
    rationale: str = Field(description="One or two sentences explaining the choice.")
    decodable_sentence: str = Field(
        description="A short, age-appropriate sentence using mostly decodable words."
    )


_TOOL_NAME = "emit_recommendation"

# Static curriculum framing — prompt-cacheable (same every call; only the learner
# state below it changes), per docs/research.md §Layer 4.
_SYSTEM = (
    "You are a structured-literacy reading coach for PreK-Grade 3. You receive a "
    "learner's mastery state and the graph-computed next-best words (each "
    "introduces exactly one new grapheme). Pick the single best target grapheme to "
    "introduce next and package decodable practice. Keep language warm, simple, and "
    "age-appropriate. Always call the tool."
)


def _tool_schema() -> dict[str, Any]:
    return {
        "name": _TOOL_NAME,
        "description": "Return the next reading recommendation for the learner.",
        "input_schema": Recommendation.model_json_schema(),
    }


def _build_context(db: Neo4jDB, learner_id: str) -> dict[str, Any]:
    """Gather the graph facts the LLM will narrate."""
    summary = recommend.mastery_summary(db, learner_id)
    nbw = recommend.next_best_word(db, learner_id, limit=8)
    rem = recommend.remediation(db, learner_id, limit=5)
    return {"summary": summary, "next_best_words": nbw, "remediation": rem}


def _fallback(context: dict[str, Any]) -> Recommendation:
    """Deterministic recommendation used when no LLM is configured."""
    nbw = context["next_best_words"]
    if not nbw:
        return Recommendation(
            target_skill="(none)",
            words=[],
            rationale="This learner can already decode the available words; "
            "move to fluency practice.",
            decodable_sentence="",
        )
    target = nbw[0]["introduces"]
    words = [r["word"] for r in nbw if r["introduces"] == target][:5]
    name = context["summary"].get("name", "the learner")
    return Recommendation(
        target_skill=target,
        words=words,
        rationale=(
            f"{name} has mastered every grapheme in these words except '{target}', "
            f"so introducing '{target}' adds exactly one new skill (i+1)."
        ),
        decodable_sentence="",
    )


def explain(
    db: Neo4jDB,
    learner_id: str,
    settings: Settings | None = None,
    client: Any | None = None,
) -> Recommendation:
    """Produce a structured recommendation for a learner.

    Uses Claude when an API key is configured; otherwise returns a deterministic
    offline recommendation. ``client`` may be injected for testing.
    """
    settings = settings or get_settings()
    context = _build_context(db, learner_id)

    if client is None and not settings.anthropic_api_key:
        log.info("agent.fallback", learner=learner_id, reason="no_api_key")
        return _fallback(context)

    if client is None:
        from anthropic import Anthropic

        client = Anthropic(api_key=settings.anthropic_api_key)

    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
        tools=[_tool_schema()],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": (
                    "Learner state and graph recommendations (JSON):\n"
                    f"{context}\n\nCall {_TOOL_NAME} with your recommendation."
                ),
            }
        ],
    )
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
            return Recommendation.model_validate(block.input)
    raise ValueError("Model did not return a tool_use recommendation.")


# -- personalized lesson generation (the LLM's real value-add) --------------------


class Lesson(BaseModel):
    """A personalized, decodable mini-lesson for one learner + target skill."""

    title: str = Field(description="A short, playful title.")
    story: str = Field(description="2-3 short decodable sentences themed to the child's interests.")
    words_used: list[str] = Field(description="Which provided safe words appear in the story.")
    teacher_note: str = Field(description="One sentence telling the teacher what this practices.")


_LESSON_TOOL = "emit_lesson"

# Cacheable framing. The hard constraint (decodability) is enforced by the word
# list the graph hands in; the LLM only personalizes the theme around it.
_LESSON_SYSTEM = (
    "You are a warm early-reading coach writing a tiny decodable story for a young "
    "child (PreK-Grade 3). You are given a TARGET phonics skill, a list of SAFE WORDS "
    "the child can already decode, and the child's interests. Hard rules: build the "
    "story almost entirely from the SAFE WORDS; you may add only the most common "
    "sight words (the, a, is, to, and, has, his, her, in, on, can, said). Keep it 2-3 "
    "short sentences, joyful, and themed to the child's interests so it feels made for "
    "them. Always call the tool."
)


def _lesson_tool_schema() -> dict[str, Any]:
    return {
        "name": _LESSON_TOOL,
        "description": "Return a personalized decodable mini-lesson.",
        "input_schema": Lesson.model_json_schema(),
    }


def _lesson_fallback(persona: dict[str, Any], target_skill: str, words: list[str]) -> Lesson:
    """Deterministic lesson when no LLM is configured (keeps the app runnable)."""
    name = persona.get("name", "Friend")
    interests = persona.get("interests") or ["fun things"]
    sample = ", ".join(words[:4]) if words else "new words"
    return Lesson(
        title=f"{name}'s '{target_skill}' words",
        story=(
            f"(Offline preview — set ANTHROPIC_API_KEY for a live, {interests[0]}-themed "
            f"story.) Practice words for '{target_skill}': {sample}."
        ),
        words_used=words[:4],
        teacher_note=f"Practices the '{target_skill}' grapheme using already-decodable words.",
    )


def offline_lesson(db: Neo4jDB, learner_id: str, target_skill: str, words: list[str]) -> Lesson:
    """Deterministic offline lesson (no network) — used as a graceful fallback."""
    persona = recommend.get_learner(db, learner_id)
    return _lesson_fallback(persona, target_skill, words)


def generate_lesson(
    db: Neo4jDB,
    learner_id: str,
    target_skill: str,
    words: list[str],
    settings: Settings | None = None,
    client: Any | None = None,
) -> Lesson:
    """Generate a personalized decodable lesson for a learner and target skill.

    The ``words`` are the graph's guaranteed-decodable safe set — the LLM may only
    theme a story around them (plus a few sight words). This is the "graph
    guarantees safe, LLM makes it personal" split made concrete.
    """
    settings = settings or get_settings()
    persona = recommend.get_learner(db, learner_id)

    if client is None and not settings.anthropic_api_key:
        log.info("agent.lesson_fallback", learner=learner_id, reason="no_api_key")
        return _lesson_fallback(persona, target_skill, words)

    if client is None:
        from anthropic import Anthropic

        client = Anthropic(api_key=settings.anthropic_api_key)

    user = (
        f"Child: {persona.get('name')} (age {persona.get('age')}).\n"
        f"Interests: {', '.join(persona.get('interests') or [])}.\n"
        f"TARGET skill (new grapheme to feature): '{target_skill}'.\n"
        f"SAFE WORDS (decodable for this child): {', '.join(words)}.\n\n"
        f"Write the story, then call {_LESSON_TOOL}."
    )
    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=1024,
        system=[{"type": "text", "text": _LESSON_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        tools=[_lesson_tool_schema()],
        tool_choice={"type": "tool", "name": _LESSON_TOOL},
        messages=[{"role": "user", "content": user}],
    )
    for block in message.content:
        if getattr(block, "type", None) == "tool_use" and block.name == _LESSON_TOOL:
            log.info("agent.lesson", learner=learner_id, skill=target_skill)
            return Lesson.model_validate(block.input)
    raise ValueError("Model did not return a tool_use lesson.")
