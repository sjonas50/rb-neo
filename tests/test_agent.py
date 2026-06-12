"""Tests for the LLM agent layer. No network: the client is mocked or absent."""

from __future__ import annotations

from types import SimpleNamespace

from rb_neo import agent
from rb_neo.agent import Lesson, Recommendation, _fallback, _lesson_fallback
from rb_neo.config import Settings


def _fake_context() -> dict:
    return {
        "summary": {"name": "Ava", "level": "beginner", "skills": 25, "mastered": 21},
        "next_best_words": [
            {"word": "ship", "introduces": "sh", "introduces_type": "digraph", "units": 3},
            {"word": "shop", "introduces": "sh", "introduces_type": "digraph", "units": 3},
        ],
        "remediation": [{"grapheme": "u", "type": "letter", "misses": 3}],
    }


def test_fallback_is_schema_valid_and_picks_target() -> None:
    rec = _fallback(_fake_context())
    assert isinstance(rec, Recommendation)
    assert rec.target_skill == "sh"
    assert rec.words == ["ship", "shop"]
    assert "sh" in rec.rationale


def test_fallback_handles_no_candidates() -> None:
    ctx = _fake_context()
    ctx["next_best_words"] = []
    rec = _fallback(ctx)
    assert rec.target_skill == "(none)"
    assert rec.words == []


class _FakeClient:
    """Mimics the Anthropic client returning a forced tool_use block."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, **kwargs):  # noqa: ANN003, ANN201
        # Assert we forced our tool and cached the system prompt.
        assert kwargs["tool_choice"]["name"] == agent._TOOL_NAME
        assert kwargs["system"][0]["cache_control"]["type"] == "ephemeral"
        block = SimpleNamespace(
            type="tool_use",
            name=agent._TOOL_NAME,
            input={
                "target_skill": "sh",
                "words": ["ship", "shop"],
                "rationale": "Introduces the /sh/ digraph; everything else is known.",
                "decodable_sentence": "The ship is in the shop.",
            },
        )
        return SimpleNamespace(content=[block])


class _FakeDB:
    """Stub DB so _build_context can be bypassed via monkeypatch."""


def test_explain_with_mocked_client(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_build_context", lambda db, lid: _fake_context())
    settings = Settings(anthropic_api_key="test-key")
    rec = agent.explain(_FakeDB(), "ava", settings=settings, client=_FakeClient())
    assert rec.target_skill == "sh"
    assert rec.decodable_sentence == "The ship is in the shop."


def test_explain_falls_back_without_key(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_build_context", lambda db, lid: _fake_context())
    settings = Settings(anthropic_api_key="")
    rec = agent.explain(_FakeDB(), "ava", settings=settings)
    assert rec.target_skill == "sh"  # deterministic fallback, no network


def test_build_context_shape() -> None:
    # _build_context just wires recommender calls; smoke-test the shape via fake.
    ctx = _fake_context()
    assert set(ctx) == {"summary", "next_best_words", "remediation"}


def test_tool_schema_matches_model() -> None:
    schema = agent._tool_schema()
    assert schema["name"] == agent._TOOL_NAME
    props = schema["input_schema"]["properties"]
    assert set(props) >= {"target_skill", "words", "rationale", "decodable_sentence"}


def test_lesson_fallback_is_schema_valid() -> None:
    persona = {"name": "Maya", "age": 6, "interests": ["space", "rockets"]}
    lesson = _lesson_fallback(persona, "sh", ["ship", "fish", "dish"])
    assert isinstance(lesson, Lesson)
    assert "sh" in lesson.title
    assert lesson.words_used == ["ship", "fish", "dish"]


def test_generate_lesson_with_mocked_client(monkeypatch) -> None:
    monkeypatch.setattr(
        agent.recommend,
        "get_learner",
        lambda db, lid: {"name": "Ben", "age": 6, "interests": ["soccer"]},
    )

    class _LessonClient:
        def __init__(self) -> None:
            self.messages = self

        def create(self, **kwargs):  # noqa: ANN003, ANN201
            assert kwargs["tool_choice"]["name"] == agent._LESSON_TOOL
            block = SimpleNamespace(
                type="tool_use",
                name=agent._LESSON_TOOL,
                input={
                    "title": "Ben's sh words",
                    "story": "The fish is on the ship. Dash!",
                    "words_used": ["fish", "ship", "dash"],
                    "teacher_note": "Practices the sh digraph.",
                },
            )
            return SimpleNamespace(content=[block])

    settings = Settings(anthropic_api_key="test-key")
    lesson = agent.generate_lesson(
        object(), "ben", "sh", ["fish", "ship", "dash"], settings=settings, client=_LessonClient()
    )
    assert lesson.title == "Ben's sh words"
    assert "ship" in lesson.words_used
