"""rb-neo showcase — investor-facing Streamlit dashboard.

The hero is a guided **graph traversal**: the recommendation is computed *on the
graph*, in front of you, with the real Cypher shown at every step —
  1. what the graph knows about the child,
  2. the graph evaluates candidates and picks the next skill (the rule is visible),
  3. learning that one skill ripples out to unlock a wave of new words,
  4. the LLM writes a personalized lesson from the graph-guaranteed safe set.

Run:
    uv pip install -e ".[showcase]"
    export ANTHROPIC_API_KEY=sk-ant-...        # for live lessons (else offline preview)
    uv run rb-neo ingest --sample 30000 && uv run rb-neo synth
    uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import streamlit as st

from rb_neo import agent, recommend, traverse
from rb_neo.config import get_settings
from rb_neo.db import Neo4jDB, Neo4jUnavailable

st.set_page_config(
    page_title="rb-neo — reading on a knowledge graph", page_icon="📚", layout="wide"
)

LEGEND = (
    "🟩 grapheme **mastered** · 🟥 the **one new** grapheme · 🟧 another not-yet grapheme · "
    "□ word (green=next-best · grey=already known · red=too hard)"
)


@st.cache_resource
def get_db() -> Neo4jDB:
    db = Neo4jDB()
    db.__enter__()
    return db


@st.cache_data(ttl=30)
def stats() -> dict:
    db = get_db()
    return {
        "words": db.query("MATCH (w:Word) RETURN count(w) AS n")[0]["n"],
        "graphemes": db.query("MATCH (g:Grapheme) RETURN count(g) AS n")[0]["n"],
        "sounds": db.query("MATCH (s:Sound) RETURN count(s) AS n")[0]["n"],
        "phonemes": db.query("MATCH (p:Phoneme) RETURN count(p) AS n")[0]["n"],
    }


def learner_options(db: Neo4jDB) -> dict[str, dict]:
    return {f"{ln['emoji']} {ln['name']} ({ln['level']})": ln for ln in recommend.list_learners(db)}


def cypher_block(step: traverse.Step) -> None:
    with st.expander("🔎 The Cypher that ran (one traversal — not app logic)", expanded=True):
        st.code(step.cypher, language="cypher")
        st.caption(f"params: `{step.params}`")


_CHIP_COLOR = {
    "mastered": ("#2e8b57", "white"),
    "target": ("#d9534f", "white"),
    "other_new": ("#e0a458", "black"),
}


def chip_rows(title: str, chips: list[dict], verdict_html: str) -> None:
    """Render candidate words as colored letter-chips with a verdict (the rule, made obvious)."""
    if not chips:
        return
    st.markdown(f"**{title}**")
    for c in chips:
        spans = "".join(
            f'<span style="background:{_CHIP_COLOR[s][0]};color:{_CHIP_COLOR[s][1]};'
            f"padding:3px 9px;margin:2px;border-radius:5px;font-family:monospace;"
            f'font-weight:600;">{ltr}</span>'
            for ltr, s in c["letters"]
        )
        st.markdown(
            f'<div style="margin:4px 0;">'
            f'<span style="display:inline-block;width:70px;font-weight:600;">{c["word"]}</span>'
            f"{spans}"
            f'<span style="margin-left:12px;color:#555;">{verdict_html}</span></div>',
            unsafe_allow_html=True,
        )


def render_lesson(db: Neo4jDB, lid: str, target: str, words: list[str], who: str) -> None:
    """Generate + render a lesson, falling back gracefully if the LLM call fails."""
    try:
        with st.spinner(f"Writing a lesson for {who}…"):
            lesson = agent.generate_lesson(db, lid, target, words)
    except Exception as exc:  # noqa: BLE001 — never let a live API error break the demo
        st.warning(
            f"Live Claude call failed ({type(exc).__name__}: {exc}). "
            "Check ANTHROPIC_API_KEY — showing the offline preview.",
            icon="⚠️",
        )
        lesson = agent.offline_lesson(db, lid, target, words)
    st.markdown(f"**📖 {lesson.title}**")
    st.write(lesson.story)
    st.info(f"👩‍🏫 {lesson.teacher_note}")


def persona_card(learner: dict, summary: dict) -> None:
    st.markdown(f"### {learner['emoji']} {learner['name']}")
    st.caption(f"Age {learner['age']} · {learner['level']}")
    st.write("**Loves:** " + ", ".join(learner.get("interests") or []))
    mastered, skills = summary.get("mastered", 0), summary.get("skills", 0) or 1
    st.progress(mastered / skills, text=f"{mastered}/{skills} graphemes mastered")


# ── connect ───────────────────────────────────────────────────────────────────
try:
    db = get_db()
    learners = learner_options(db)
except Neo4jUnavailable as exc:
    st.error(str(exc))
    st.stop()
if not learners:
    st.warning("No learners. Run `uv run rb-neo ingest --sample 30000` then `rb-neo synth`.")
    st.stop()

settings = get_settings()
live = bool(settings.anthropic_api_key)

# ── header ────────────────────────────────────────────────────────────────────
st.title("📚 Reading Personalization — *Watch the Knowledge Graph Decide*")
s = stats()
cols = st.columns(4)
cols[0].metric("Words in graph", f"{s['words']:,}")
cols[1].metric("Graphemes", s["graphemes"])
cols[2].metric("Shared sounds", s["sounds"])
cols[3].metric("Phonemes", s["phonemes"])
st.caption(
    f"{s['words']:,} words collapse to {s['sounds']} shared sounds & {s['phonemes']} phonemes — "
    "that reuse is the engine below. "
    + ("🟢 Live Claude lessons." if live else "🟡 No ANTHROPIC_API_KEY — offline lesson preview.")
)

tab_flow, tab_split = st.tabs(["🎯 Watch the graph decide", "🆚 Same skill, two kids"])

# ── TAB 1: the guided traversal ───────────────────────────────────────────────
with tab_flow:
    label = st.selectbox("Student", list(learners), key="flow_student")
    learner = learners[label]
    lid, lname = learner["id"], learner["name"]

    # progressive reveal, reset when the student changes
    if st.session_state.get("flow_for") != lid:
        st.session_state.flow_for = lid
        st.session_state.step = 1

    bcols = st.columns([1, 1, 6])
    if bcols[0].button("▶ Next step", type="primary"):
        st.session_state.step = min(4, st.session_state.get("step", 1) + 1)
    if bcols[1].button("↺ Restart"):
        st.session_state.step = 1
    step = st.session_state.get("step", 1)
    st.caption(LEGEND)

    # Step 1 — learner state (compact chips, not a giant fan)
    st.markdown("#### 1 · What the graph knows about this child")
    s1 = traverse.step_learner_state(db, lid, lname)
    mastered = list(dict.fromkeys(g.lower() for g in s1.extra["mastered"]))
    pills = "".join(
        f'<span style="background:#2e8b57;color:white;padding:3px 9px;margin:3px;'
        f'border-radius:5px;font-family:monospace;font-weight:600;">{g}</span>'
        for g in mastered
    )
    st.markdown(f"<div>{pills}</div>", unsafe_allow_html=True)
    st.info(s1.note)
    cypher_block(s1)

    # Step 2 — the decision
    if step >= 2:
        st.divider()
        st.markdown("#### 2 · The graph evaluates every candidate word and picks the next skill")
        s2 = traverse.step_decision(db, lid, lname)
        if not s2.rows:
            st.success(s2.note)
            target = None
        else:
            target = s2.extra["target"]
            st.markdown(s2.note)
            chip_rows(
                f"✅ NEXT-BEST — exactly one new letter (`{target}`)",
                s2.extra["chips_accepted"],
                "✅ teach this",
            )
            chip_rows("⏭️ Already decodable — zero new letters", s2.extra["chips_known"], "skip")
            chip_rows("🚫 Too hard — two or more new letters", s2.extra["chips_hard"], "not yet")
            st.caption(
                "🟩 mastered · 🟥 the one new grapheme · 🟧 another not-yet grapheme — "
                "a word is accepted only when it has exactly one 🟥/🟧."
            )
            cypher_block(s2)
        st.session_state.flow_target = target
    else:
        st.caption("➡️ Press **Next step** to watch the graph choose what to teach.")

    # Step 3 — the ripple
    target = st.session_state.get("flow_target")
    if step >= 3 and target:
        st.divider()
        st.markdown(f"#### 3 · {lname} masters **`{target}`** — watch the graph unlock new words")
        s3 = traverse.step_ripple(db, lid, lname, target)
        m1, m2 = st.columns(2)
        m1.metric("Decodable words before", s3.extra["before"])
        m2.metric("Decodable words after", s3.extra["after"], delta=f"+{len(s3.extra['unlocked'])}")
        st.graphviz_chart(s3.dot, width="content")
        n_unlocked = len(s3.extra["unlocked"])
        if n_unlocked > 12:
            st.caption(f"…and {n_unlocked - 12} more (showing the first 12).")
        st.success(s3.note)
        cypher_block(s3)
        st.session_state.flow_unlocked = s3.extra["unlocked"]

    # Step 4 — the LLM lesson
    if step >= 4 and target:
        st.divider()
        st.markdown("#### 4 · The LLM writes a personalized lesson — *from the graph's safe set*")
        words = st.session_state.get("flow_unlocked", [])[:8]
        st.markdown("Graph-guaranteed safe words: " + " ".join(f"`{w}`" for w in words))
        st.caption("The LLM may only theme a story around these — it cannot go off-curriculum.")
        render_lesson(db, lid, target, words, lname)

# ── TAB 2: split-screen personalization ───────────────────────────────────────
with tab_split:
    st.markdown(
        "Same target skill. **Identical** graph-computed safe words. Two children → two "
        "different lessons. The graph guarantees safety; the LLM personalizes within it."
    )
    skill = st.selectbox("Target skill (digraph)", ["sh", "ch", "th", "ck"], key="split_skill")
    names = list(learners)
    cc = st.columns(2)
    pa = cc[0].selectbox("Left", names, index=min(1, len(names) - 1), key="split_a")
    pb = cc[1].selectbox("Right", names, index=min(2, len(names) - 1), key="split_b")
    la, lb = learners[pa], learners[pb]
    wa = [r["word"] for r in recommend.cross_word(db, la["id"], skill, limit=8)]
    wb = [r["word"] for r in recommend.cross_word(db, lb["id"], skill, limit=8)]

    if wa and wa == wb:
        st.success("✅ Identical safe set (graph-guaranteed): " + " ".join(f"`{w}`" for w in wa))
    elif wa and wb:
        st.warning("Safe sets differ — the two children aren't at the same mastery for this skill.")

    gen = st.button("✨ Generate both lessons", type="primary", key="split_gen")
    g1, g2 = st.columns(2)
    for col, who, words in ((g1, la, wa), (g2, lb, wb)):
        with col:
            persona_card(who, recommend.mastery_summary(db, who["id"]))
            st.markdown(" ".join(f"`{w}`" for w in words) or "_no safe words_")
            if gen and words:
                render_lesson(db, who["id"], skill, words, who["name"])
