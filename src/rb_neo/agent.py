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
