"""Build an interactive graph view (pyvis) for the showcase.

Renders a set of words decomposed into their shared graphemes and sounds, with a
learner's mastery overlaid (mastered graphemes green, not-yet red). Shared nodes
naturally connect multiple words — the "reuse" story made visible.
"""

from __future__ import annotations

from .db import Neo4jDB

_WORD_DECOMP = """
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE w.text IN $words
OPTIONAL MATCH (g)-[:PRODUCES_SOUND]->(s:Sound)
RETURN w.text AS word, g.text AS grapheme, g.type AS gtype,
       collect(DISTINCT s.id) AS sounds
"""

_MASTERED = """
MATCH (l:Learner {id: $learner_id})-[:MASTERED {mastered: true}]->(g:Grapheme)
RETURN collect(g.text) AS mastered
"""

# Colors
_C_WORD = "#1f3a5f"
_C_MASTERED = "#2e8b57"
_C_UNMASTERED = "#d9534f"
_C_NEUTRAL = "#e0a458"
_C_SOUND = "#9aa5b1"


def mastered_graphemes(db: Neo4jDB, learner_id: str) -> set[str]:
    """Return the set of grapheme texts a learner has mastered."""
    rows = db.query(_MASTERED, learner_id=learner_id)
    return set(rows[0]["mastered"]) if rows else set()


def build_word_graph_html(
    db: Neo4jDB,
    words: list[str],
    learner_id: str | None = None,
    height: str = "520px",
    show_sounds: bool = True,
) -> str:
    """Return standalone HTML for an interactive graph of ``words``.

    Args:
        db: Open database.
        words: Word texts to decompose and display.
        learner_id: If given, color graphemes by that learner's mastery.
        height: CSS height of the canvas.
        show_sounds: Whether to include shared Sound nodes.

    Returns:
        Self-contained HTML string (embed with ``streamlit.components.v1.html``).
    """
    from pyvis.network import Network

    rows = db.query(_WORD_DECOMP, words=words)
    mastered = mastered_graphemes(db, learner_id) if learner_id else set()

    net = Network(height=height, width="100%", directed=True, notebook=False)
    net.toggle_physics(True)

    seen: set[str] = set()

    def add(node_id: str, label: str, color: str, size: int, shape: str = "dot") -> None:
        if node_id not in seen:
            net.add_node(node_id, label=label, color=color, size=size, shape=shape)
            seen.add(node_id)

    for r in rows:
        wid = f"w:{r['word']}"
        add(wid, r["word"], _C_WORD, 26, shape="box")

        gid = f"g:{r['grapheme']}"
        if learner_id:
            gcolor = _C_MASTERED if r["grapheme"] in mastered else _C_UNMASTERED
        else:
            gcolor = _C_NEUTRAL
        add(gid, r["grapheme"], gcolor, 18)
        net.add_edge(wid, gid)

        if show_sounds:
            for sid_raw in r["sounds"]:
                if not sid_raw:
                    continue
                sid = f"s:{sid_raw}"
                # Short label — the mp3 id tail is enough to show sharing.
                add(sid, "♪", _C_SOUND, 10, shape="dot")
                net.add_edge(gid, sid)

    return net.generate_html(notebook=False)


def legend_markdown(personalized: bool) -> str:
    """A small legend describing the node colors."""
    if personalized:
        return (
            "🟦 word · 🟩 grapheme **mastered** · 🟥 grapheme **not yet** · ⚪ shared sound — "
            "*green/red is this learner's mastery; shared graphemes/sounds link multiple words.*"
        )
    return "🟦 word · 🟧 grapheme · ⚪ shared sound — *shared nodes link multiple words.*"
