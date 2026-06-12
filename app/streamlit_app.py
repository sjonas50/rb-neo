"""rb-neo showcase — investor-facing Streamlit dashboard.

Tells one story: **the graph guarantees what's safe to teach (deterministic,
decodable); the LLM makes it personal (a story themed to this child).**

Run:
    uv pip install -e ".[showcase]"
    uv run streamlit run app/streamlit_app.py

Expects the graph to be seeded first:
    uv run rb-neo ingest --sample 30000
    uv run rb-neo synth
"""

from __future__ import annotations

import collections

import streamlit as st
import streamlit.components.v1 as components

from rb_neo import agent, recommend, viz
from rb_neo.config import get_settings
from rb_neo.db import Neo4jDB, Neo4jUnavailable

st.set_page_config(page_title="rb-neo — personalized early reading", page_icon="📚", layout="wide")


@st.cache_resource
def get_db() -> Neo4jDB:
    """Open and cache a Neo4j connection for the session."""
    db = Neo4jDB()
    db.__enter__()
    return db


@st.cache_data(ttl=30)
def graph_stats() -> dict:
    db = get_db()
    q = "MATCH (w:Word) RETURN count(w) AS n"
    counts = {
        "words": db.query(q)[0]["n"],
        "graphemes": db.query("MATCH (g:Grapheme) RETURN count(g) AS n")[0]["n"],
        "sounds": db.query("MATCH (s:Sound) RETURN count(s) AS n")[0]["n"],
        "phonemes": db.query("MATCH (p:Phoneme) RETURN count(p) AS n")[0]["n"],
    }
    return counts


def learner_options(db: Neo4jDB) -> dict[str, dict]:
    return {f"{ln['emoji']} {ln['name']} ({ln['level']})": ln for ln in recommend.list_learners(db)}


def top_target(db: Neo4jDB, learner_id: str) -> str | None:
    """The most reinforceable next grapheme for a learner (graph-chosen)."""
    nbw = recommend.next_best_word(db, learner_id, limit=25)
    if not nbw:
        return None
    counts = collections.Counter(r["introduces"] for r in nbw)
    return counts.most_common(1)[0][0]


def persona_card(learner: dict, summary: dict) -> None:
    st.markdown(f"### {learner['emoji']} {learner['name']}")
    st.caption(f"Age {learner['age']} · {learner['level']}")
    st.write("**Loves:** " + ", ".join(learner.get("interests") or []))
    mastered, skills = summary.get("mastered", 0), summary.get("skills", 0) or 1
    st.progress(mastered / skills, text=f"{mastered}/{skills} graphemes mastered")


def render_lesson(db: Neo4jDB, learner_id: str, target: str, words: list[str]) -> None:
    lesson = agent.generate_lesson(db, learner_id, target, words)
    st.markdown(f"**📖 {lesson.title}**")
    st.write(lesson.story)
    if lesson.words_used:
        st.caption("Words used: " + ", ".join(lesson.words_used))
    st.info(f"👩‍🏫 {lesson.teacher_note}", icon="🧩")


# ── connection ────────────────────────────────────────────────────────────────
try:
    db = get_db()
    learners = learner_options(db)
except Neo4jUnavailable as exc:
    st.error(str(exc))
    st.stop()

if not learners:
    st.warning("No learners found. Run `uv run rb-neo ingest --sample 30000` then `rb-neo synth`.")
    st.stop()

settings = get_settings()
live = bool(settings.anthropic_api_key)

# ── header ────────────────────────────────────────────────────────────────────
st.title("📚 Personalized Early Reading — on a Knowledge Graph")
st.markdown("#### The graph guarantees what's *safe* to teach. The LLM makes it *personal.*")
s = graph_stats()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Words", f"{s['words']:,}")
c2.metric("Distinct graphemes", s["graphemes"])
c3.metric("Distinct sounds", s["sounds"])
c4.metric("Distinct phonemes", s["phonemes"])
_llm_status = (
    "🟢 Live Claude generation ON."
    if live
    else "🟡 No ANTHROPIC_API_KEY — lessons show an offline preview."
)
st.caption(
    f"~{s['words']:,} words decode to just {s['sounds']} shared sounds and {s['phonemes']} "
    f"phonemes — that reuse is why a graph fits. {_llm_status}"
)

tab_dash, tab_split, tab_graph = st.tabs(
    ["👧 Student dashboard", "🆚 Same skill, two kids", "🕸️ The graph"]
)

# ── Tab 1: single student dashboard ───────────────────────────────────────────
with tab_dash:
    label = st.selectbox("Student", list(learners), key="dash_student")
    learner = learners[label]
    lid = learner["id"]
    summary = recommend.mastery_summary(db, lid)

    left, right = st.columns([1, 2])
    with left:
        persona_card(learner, summary)

    with right:
        target = top_target(db, lid)
        if not target:
            st.success("This learner can decode every available word — move to fluency practice.")
        else:
            words = [r["word"] for r in recommend.cross_word(db, lid, target, limit=8)]
            st.markdown("##### 🔵 GRAPH — *deterministic, guaranteed decodable*")
            st.markdown(f"Next target skill: **`{target}`**")
            st.markdown("Safe practice words (every other letter already mastered):")
            st.markdown(" ".join(f"`{w}`" for w in words) or "_none_")

            st.markdown("##### 🟢 LLM — *personalized within that safe set*")
            if st.button("✨ Generate personalized lesson", key="dash_gen", type="primary"):
                with st.spinner("Writing a lesson just for this child…"):
                    render_lesson(db, lid, target, words)

    with st.expander("🕸️ See the graph: these words → shared letters & sounds (mastery overlaid)"):
        if target:
            html = viz.build_word_graph_html(db, words, learner_id=lid, height="420px")
            st.caption(viz.legend_markdown(personalized=True))
            components.html(html, height=440)

# ── Tab 2: split-screen (the money shot) ──────────────────────────────────────
with tab_split:
    st.markdown(
        "Same target skill. **Identical** graph-computed safe words. "
        "Two children → two different lessons. *That's the whole idea.*"
    )
    skill = st.selectbox("Target skill (digraph)", ["sh", "ch", "th", "ck"], key="split_skill")

    names = list(learners)
    cc = st.columns(2)
    pick_a = cc[0].selectbox("Left student", names, index=min(2, len(names) - 1), key="split_a")
    pick_b = cc[1].selectbox("Right student", names, index=min(3, len(names) - 1), key="split_b")
    la, lb = learners[pick_a], learners[pick_b]

    words_a = [r["word"] for r in recommend.cross_word(db, la["id"], skill, limit=8)]
    words_b = [r["word"] for r in recommend.cross_word(db, lb["id"], skill, limit=8)]

    if words_a and words_a == words_b:
        st.success(
            "✅ Identical safe set for both — the graph guarantees decodability: "
            + " ".join(f"`{w}`" for w in words_a)
        )
    else:
        st.warning("Safe sets differ (the two students aren't at the same mastery for this skill).")

    gen = st.button("✨ Generate both lessons", key="split_gen", type="primary")
    col_a, col_b = st.columns(2)
    for col, learner_, words_ in ((col_a, la, words_a), (col_b, lb, words_b)):
        with col:
            persona_card(learner_, recommend.mastery_summary(db, learner_["id"]))
            st.markdown(" ".join(f"`{w}`" for w in words_) or "_no safe words_")
            if gen and words_:
                with st.spinner(f"Writing for {learner_['name']}…"):
                    render_lesson(db, learner_["id"], skill, words_)

# ── Tab 3: the graph ──────────────────────────────────────────────────────────
with tab_graph:
    st.markdown("Pick words and watch them share letters and sounds. Overlay a student's mastery.")
    sample_words = [r["word"] for r in recommend.mastery_aware(db, "ben", limit=30)][:12]
    chosen = st.multiselect("Words", sample_words, default=sample_words[:6])
    overlay = st.selectbox("Mastery overlay", ["(none)"] + list(learners), key="graph_overlay")
    lid = learners[overlay]["id"] if overlay != "(none)" else None
    if chosen:
        html = viz.build_word_graph_html(db, chosen, learner_id=lid, height="560px")
        st.caption(viz.legend_markdown(personalized=lid is not None))
        components.html(html, height=580)
