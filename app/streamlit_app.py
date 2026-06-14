"""rb-neo showcase — watch the knowledge graph compute a child's ZPD.

The hero is the research-validated personalization loop, run live on the graph
with the real Cypher visible at every step:
  1. the curriculum is a prerequisite DAG; the child's mastery colors it —
     the zone of proximal development is *visible* (gold),
  2. one traversal ranks the ZPD skills by leverage (words each would unlock)
     and picks the next skill — deterministic, auditable, zero LLM,
  3. i+1 word selection: practice words with exactly one new grapheme,
  4. the ripple: one skill unlocks a wave of words — and the graph already
     knows their rhyme families and minimal pairs,
  5. the LLM writes a personalized lesson from the graph-guaranteed safe set.

Every panel reads the graph built from words/*.json — no hard-coded word data.

Run:
    docker compose up -d                       # Neo4j
    uv pip install -e ".[showcase]"
    export ANTHROPIC_API_KEY=sk-ant-...        # for live lessons (else offline preview)
    uv run rb-neo init                         # schema + curriculum DAG
    uv run rb-neo ingest --sample 30000 && uv run rb-neo synth
    uv run streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import streamlit as st

from rb_neo import agent, recommend, traverse
from rb_neo.config import get_settings
from rb_neo.db import Neo4jDB, Neo4jUnavailable
from rb_neo.traversal_player import build_traversal_html
from rb_neo.wordlists import ANATOMY_WORDS

st.set_page_config(
    page_title="rb-neo — reading on a knowledge graph", page_icon="📚", layout="wide"
)

N_STEPS = 5

KIND_LABEL = {
    "letter": "letter",
    "double": "double consonant",
    "digraph": "digraph",
    "r_controlled": "r-controlled vowel",
    "vowel_team": "vowel team",
    "grapheme": "grapheme",
}


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
        "skills": db.query("MATCH (s:Skill) RETURN count(s) AS n")[0]["n"],
        "prereqs": db.query("MATCH (:Skill)-[r:PREREQUISITE_OF]->(:Skill) RETURN count(r) AS n")[0][
            "n"
        ],
        "rimes": db.query("MATCH (r:Rime) RETURN count(r) AS n")[0]["n"],
        "pairs": db.query("MATCH ()-[m:MINIMAL_PAIR_OF]-() RETURN count(m)/2 AS n")[0]["n"],
    }


def learner_options(db: Neo4jDB) -> dict[str, dict]:
    return {f"{ln['emoji']} {ln['name']} ({ln['level']})": ln for ln in recommend.list_learners(db)}


@st.cache_data(ttl=60, show_spinner=False)
def player_file(lid: str, name: str, emoji: str) -> Path:
    """Build the traversal animation and persist it for ``st.iframe``."""
    html = build_traversal_html(get_db(), lid, name, emoji=emoji)
    path = Path(tempfile.gettempdir()) / f"rbneo_player_{lid}.html"
    path.write_text(html, encoding="utf-8")
    return path


@st.cache_data(ttl=300, show_spinner=False)
def profile_rows(cypher: str, params_key: str) -> list[dict]:
    return get_db().profile(cypher, **json.loads(params_key))


def cypher_block(step: traverse.Step, expanded: bool = False) -> None:
    with st.expander("🔎 The Cypher that ran (one traversal — not app logic)", expanded=expanded):
        st.code(step.cypher, language="cypher")
        st.caption(f"params: `{step.params}`")


def profile_block(cypher: str, params: dict) -> None:
    """Neo4j's own PROFILE plan — proof the work happens in the database engine."""
    with st.expander("⚙️ The engine's execution plan (`PROFILE` — real operator counters)"):
        rows = profile_rows(cypher, json.dumps(params, sort_keys=True))
        total_hits = sum(r["db_hits"] or 0 for r in rows)
        lines = ["```", f"{'operator':<42} {'rows':>8} {'db hits':>10}", "-" * 62]
        for r in rows:
            name = "  " * r["depth"] + r["operator"].replace("@neo4j", "")
            rows_v = "-" if r["rows"] is None else f"{r['rows']:,}"
            hits_v = "-" if r["db_hits"] is None else f"{r['db_hits']:,}"
            lines.append(f"{name:<42} {rows_v:>8} {hits_v:>10}")
        lines.append("```")
        st.markdown("\n".join(lines))
        st.caption(
            f"{len(rows)} operators · {total_hits:,} db hits — this is the database engine "
            "traversing index-backed relationships, not application code looping over rows."
        )


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


def skill_chip(text: str, bg: str, fg: str = "white") -> str:
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 9px;margin:3px;'
        f"border-radius:5px;font-family:monospace;font-weight:600;"
        f'display:inline-block;">{text}</span>'
    )


def render_lesson(db: Neo4jDB, lid: str, target: str, words: list[str], who: str) -> agent.Lesson:
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
    return lesson


_AUDIT_STYLE = {
    "practice": ("#d9534f", "white", "the new skill"),
    "decodable": ("#2e8b57", "white", "already decodable"),
    "sight": ("#9aa5b1", "white", "allowed sight word"),
    "flagged": ("#fff3cd", "#b30000", "⚠ off-curriculum"),
}


def render_audit(db: Neo4jDB, lid: str, story: str, safe_words: list[str], who: str) -> None:
    """The graph re-checks every word the LLM wrote against this child's mastery."""
    audit = agent.audit_lesson(db, lid, story, safe_words=safe_words)
    if not audit:
        return
    spans = "".join(
        f'<span style="background:{_AUDIT_STYLE[a["status"]][0]};'
        f"color:{_AUDIT_STYLE[a['status']][1]};padding:2px 7px;margin:2px;"
        f'border-radius:5px;font-family:monospace;">{a["word"]}</span>'
        for a in audit
    )
    st.markdown("**🛡️ Graph audit of the AI's story (every word, re-checked):**")
    st.markdown(f"<div style='line-height:2.1'>{spans}</div>", unsafe_allow_html=True)
    flagged = [a["word"] for a in audit if a["status"] == "flagged"]
    if flagged:
        st.warning(
            f"The LLM used {len(set(flagged))} word(s) outside {who}'s curriculum: "
            + ", ".join(f"`{w}`" for w in dict.fromkeys(flagged))
            + " — caught by the same graph that chose the safe set.",
            icon="🛡️",
        )
    else:
        st.success(
            f"Audit passed: every word is the practice skill, already decodable for {who}, "
            "or an allowed sight word. The graph verified the AI's output.",
            icon="🛡️",
        )
    st.caption(
        "🟥 practice (the new skill) · 🟩 already decodable · ⬜ sight word · 🟨 flagged. "
        "The same mastery overlay that selected the words audits the generated story — "
        "AI creativity inside deterministic guardrails."
    )


def funnel_bars(stages: list[dict]) -> None:
    """Real row counts narrowing through the query, clause by clause."""
    top = stages[0]["count"] or 1
    for i, f in enumerate(stages):
        # sqrt scale keeps the 30k→8 collapse visible without flattening the tail
        width = max(8, int(420 * (f["count"] / top) ** 0.5))
        color = "#1f3a5f" if i < len(stages) - 1 else "#2e8b57"
        st.markdown(
            f'<div style="margin:4px 0;display:flex;align-items:center;">'
            f'<span style="display:inline-block;width:{width}px;height:20px;'
            f'background:{color};border-radius:4px;margin-right:10px;"></span>'
            f'<b style="min-width:70px;">{f["count"]:,}</b>'
            f'<span style="color:#555;margin-left:8px;">{f["label"]} '
            f'<code style="font-size:12px;">{f["clause"]}</code></span></div>',
            unsafe_allow_html=True,
        )


def persona_card(learner: dict, summary: dict) -> None:
    st.markdown(f"### {learner['emoji']} {learner['name']}")
    st.caption(f"Age {learner['age']} · {learner['level']}")
    st.write("**Loves:** " + ", ".join(learner.get("interests") or []))
    mastered, skills = summary.get("mastered", 0), summary.get("skills", 0) or 1
    st.progress(mastered / skills, text=f"{mastered}/{skills} curriculum skills mastered")


# ── connect ───────────────────────────────────────────────────────────────────
try:
    db = get_db()
    learners = learner_options(db)
except Neo4jUnavailable as exc:
    st.error(str(exc))
    st.stop()
if not learners:
    st.warning(
        "No learners. Run `uv run rb-neo init`, `uv run rb-neo ingest --sample 30000`, "
        "then `uv run rb-neo synth`."
    )
    st.stop()

settings = get_settings()
live = bool(settings.anthropic_api_key)

# ── header ────────────────────────────────────────────────────────────────────
st.title("📚 Reading Personalization — *Watch the Knowledge Graph Compute a Child's ZPD*")
s = stats()
cols = st.columns(5)
cols[0].metric("Words in graph", f"{s['words']:,}")
cols[1].metric("Curriculum skills", s["skills"])
cols[2].metric("Prerequisite edges", s["prereqs"])
cols[3].metric("Rhyme families", f"{s['rimes']:,}")
cols[4].metric("Minimal pairs", f"{s['pairs']:,}")
st.caption(
    f"{s['words']:,} words share {s['graphemes']} grapheme nodes; a hand-authored phonics "
    "curriculum sits on top as a **prerequisite DAG**. The child's zone of proximal "
    "development — *not too easy, not too hard, learnable right now* — is a graph query. "
    + ("🟢 Live Claude lessons." if live else "🟡 No ANTHROPIC_API_KEY — offline lesson preview.")
)

tab_flow, tab_anatomy, tab_split = st.tabs(
    ["🎯 Watch the graph decide", "🔬 Anatomy of a word", "🆚 Same skill, two kids"]
)

# ── TAB 1: the guided ZPD traversal ───────────────────────────────────────────
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
        st.session_state.step = min(N_STEPS, st.session_state.get("step", 1) + 1)
    if bcols[1].button("↺ Restart"):
        st.session_state.step = 1
    step = st.session_state.get("step", 1)

    # Step 1 — the skill map: ZPD made visible
    st.markdown(f"#### 1 · {lname}'s position on the curriculum graph")
    s1 = traverse.step_skill_map(db, lid, lname)
    st.markdown(s1.note)
    st.graphviz_chart(s1.dot, width="stretch")
    st.caption(
        "🟩 mastered · 🟨 **the ZPD** (every prerequisite mastered — teachable today) · "
        "⬜ locked (a prerequisite is missing). Arrows are PREREQUISITE_OF edges: "
        "*sh* needs *s* and *h*; *ar* needs *a* and *r*."
    )

    # Step 2 — the ZPD decision, replayed live on the graph
    if step >= 2:
        st.divider()
        st.markdown("#### 2 · Watch the traversal happen — live replay on the real subgraph")
        s2 = traverse.step_zpd_decision(db, lid, lname)
        if not s2.rows:
            st.success(s2.note)
            target = None
        else:
            target = s2.extra["target"]
            st.iframe(
                player_file(lid, lname, learner.get("emoji") or "🧒"),
                height=760,
            )
            st.caption(
                "Every frame is real query data — the mastery wave, the prerequisite check, "
                "the leverage scores, and each word's accept/reject are replays of the rows "
                "Neo4j returned, not a canned animation."
            )
            st.markdown(s2.note)
            max_u = max(r["unlocks"] for r in s2.extra["pool"]) or 1
            for i, r in enumerate(s2.extra["pool"]):
                bar = int(160 * r["unlocks"] / max_u)
                color = "#d9534f" if i == 0 else "#e8a33d"
                st.markdown(
                    '<div style="margin:3px 0;">'
                    + skill_chip(r["skill"], color)
                    + f'<span style="display:inline-block;width:{max(bar, 2)}px;height:10px;'
                    f'background:{color};opacity:.45;border-radius:3px;margin:0 8px;"></span>'
                    f'<span style="color:#555;">+{r["unlocks"]} words · '
                    f"{KIND_LABEL.get(r['kind'], r['kind'])}"
                    f"{' · ✅ chosen' if i == 0 else ''}</span></div>",
                    unsafe_allow_html=True,
                )
            if s2.extra["locked"]:
                locked_html = " ".join(
                    skill_chip(
                        f"🔒 {r['skill']} ← needs {', '.join(r['missing'])}", "#eeeeee", "#777"
                    )
                    for r in s2.extra["locked"][:6]
                )
                st.markdown(
                    f'<div style="margin-top:8px;">{locked_html}</div>', unsafe_allow_html=True
                )
                st.caption(
                    "Locked skills are *outside* the ZPD — the prerequisite edges keep the "
                    "recommender from ever suggesting them too early."
                )
            cypher_block(s2, expanded=False)
            profile_block(s2.cypher, s2.params)
        st.session_state.flow_target = target
    else:
        st.caption("➡️ Press **Next step** to watch the graph choose what to teach.")

    # Step 3 — i+1 word selection
    target = st.session_state.get("flow_target")
    if step >= 3 and target:
        st.divider()
        st.markdown(f"#### 3 · Pick practice words: **'{target}'** must be the *only* new thing")
        s3 = traverse.step_words(db, lid, lname, target)
        st.markdown("**The query, as a funnel — real row counts at every clause:**")
        funnel_bars(traverse.funnel(db, lid, target))
        st.caption(
            "The output list is the residue of graph-side narrowing — no application "
            "logic touches the candidate set."
        )
        st.markdown(s3.note)
        chip_rows(
            f"✅ i+1 practice — exactly one new grapheme (`{target}`)",
            s3.extra["chips_accepted"],
            "✅ teach with this",
        )
        chip_rows("⏭️ Already decodable — nothing new to learn", s3.extra["chips_known"], "skip")
        chip_rows(
            "🚫 Outside the ZPD — two or more new graphemes", s3.extra["chips_hard"], "not yet"
        )
        st.caption(
            "🟩 mastered grapheme · 🟥 the one new target · 🟧 another not-yet grapheme — "
            "a word qualifies only when it has exactly one non-green chip."
        )
        cypher_block(s3)

    # Step 4 — the ripple + the structure the graph already knows
    if step >= 4 and target:
        st.divider()
        st.markdown(f"#### 4 · {lname} masters **'{target}'** — watch the graph unlock new words")
        s4 = traverse.step_ripple(db, lid, lname, target)
        m1, m2 = st.columns(2)
        m1.metric("Decodable words before", s4.extra["before"])
        m2.metric("Decodable words after", s4.extra["after"], delta=f"+{len(s4.extra['unlocked'])}")
        st.graphviz_chart(s4.dot, width="content")
        n_unlocked = len(s4.extra["unlocked"])
        if n_unlocked > 12:
            st.caption(f"…and {n_unlocked - 12} more (showing the first 12).")
        st.success(s4.note)
        e1, e2 = st.columns(2)
        with e1:
            st.markdown("**🎵 Rhyme families** *(shared `Rime` node — grouped by sound)*")
            for r in s4.extra["rimes"]:
                st.markdown("· " + " ".join(f"`{w}`" for w in r["members"]))
        with e2:
            st.markdown("**🔄 Minimal pairs** *(differ by exactly one phoneme)*")
            for p in s4.extra["pairs"]:
                st.markdown(f"· `{p['word']}` ↔ " + " ".join(f"`{w}`" for w in p["pairs"]))
        st.caption(
            "No one curated these groupings — they are `HAS_RIME` and `MINIMAL_PAIR_OF` "
            "edges computed at ingest. Ready-made discrimination and word-family practice."
        )
        cypher_block(s4)
        st.session_state.flow_unlocked = s4.extra["unlocked"]

    # Step 5 — the full graph → AI → graph loop
    if step >= N_STEPS and target:
        st.divider()
        st.markdown("#### 5 · The graph → AI → graph loop: constrain, generate, verify")
        words = st.session_state.get("flow_unlocked", [])[:8]
        c_in, c_out = st.columns([1, 1])
        with c_in:
            st.markdown("**📤 What the graph hands Claude** *(the entire payload — verbatim)*")
            st.code(
                json.dumps(agent.lesson_context(db, lid, target, words), indent=2),
                language="json",
            )
            st.caption(
                "The AI never sees the database. It receives persona facts plus the "
                "graph-computed safe set — and may not write outside it."
            )
        with c_out:
            st.markdown("**📥 What Claude writes back** *(structured tool output)*")
            lesson = render_lesson(db, lid, target, words, lname)
        st.divider()
        render_audit(db, lid, f"{lesson.title} {lesson.story}", words, lname)

# ── TAB: anatomy of a word — the full linguistic hierarchy ────────────────────
with tab_anatomy:
    st.markdown(
        "Every word is the *same word at six levels of granularity*. The graph connects "
        "them: **Word → Syllables → Chunks → Graphemes → Phonemes**, with the shared "
        "audio each level plays. One traversal renders the whole nested structure."
    )
    examples = ANATOMY_WORDS + ["ship", "fish", "chest"]
    ac = st.columns([2, 3])
    pick = ac[0].selectbox("Example word", examples, key="anat_pick")
    typed = ac[1].text_input("…or type any word in the corpus", key="anat_typed").strip().lower()
    word = typed or pick
    anat = traverse.word_anatomy(db, word)
    if not anat.rows:
        st.warning(anat.note)
    else:
        st.markdown(anat.note)
        lv = anat.extra["levels"]
        m = st.columns(5)
        m[0].metric("Syllables", len(lv["sylls"]))
        m[1].metric("Chunks", len(lv["chunks"]))
        m[2].metric("Graphemes", len(lv["graphemes"]))
        m[3].metric("Phonemes", len(lv["phonemes"]))
        m[4].metric("Units with audio 🔊", len(anat.extra["sounded"]))
        st.graphviz_chart(anat.dot, width="stretch")
        st.caption(
            "🟦 word · 🟪 syllable · 🟦 chunk · 🟩 grapheme · 🟧 phoneme. 🔊 = the unit "
            "carries its own shared audio. Gold edges are grapheme→phoneme correspondences "
            "(GPC) — shown where the alignment is unambiguously 1:1; phonemes with a dashed "
            "link have an ambiguous mapping (silent-e, x→/k+s/) and are tied to the word."
        )
        with st.expander("🔎 The traversal (Word → Syllable → Chunk → Grapheme → Phoneme)"):
            st.code(anat.cypher, language="cypher")
            st.caption(f"params: `{anat.params}`")
        st.info(
            "Because every sub-word node is shared (MERGE-on-key), these same syllable, "
            "chunk, grapheme, sound, and phoneme nodes are reused across thousands of "
            "other words — this anatomy is one slice through a dense, shared graph."
        )

# ── TAB: split-screen personalization ─────────────────────────────────────────
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
                lesson = render_lesson(db, who["id"], skill, words, who["name"])
                render_audit(db, who["id"], f"{lesson.title} {lesson.story}", words, who["name"])
