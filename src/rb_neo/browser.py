"""Curated Cypher queries for exploring the graph in Neo4j Browser.

These are the demo's queries, hand-picked to tell the knowledge-graph story
interactively at http://localhost:7474 — structure (the curriculum DAG, shared
sub-word units), the learner overlay, and the recommender's traversals. Each
returns whole nodes/paths so the Browser renders a graph rather than a table.

This module is the single source of truth: ``rb-neo browser-queries`` prints
these, and ``docs/neo4j-queries.md`` is generated from the same list (run
``rb-neo browser-queries --markdown``), so the two never drift.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserQuery:
    """One curated, copy-pasteable Browser query."""

    key: str
    title: str
    why: str
    cypher: str
    tip: str = ""


# Ordered for a guided walk: scale -> structure -> learner overlay -> the
# recommender traversals -> the derived phonological structure.
BROWSER_QUERIES: list[BrowserQuery] = [
    BrowserQuery(
        key="scale",
        title="Scale & reuse — why a graph at all",
        why=(
            "Tens of thousands of words collapse onto a tiny shared vocabulary of "
            "sub-word units. That reuse is the whole argument for a graph."
        ),
        cypher=(
            "MATCH (w:Word)   WITH count(w)  AS words\n"
            "MATCH (g:Grapheme) WITH words, count(g) AS graphemes\n"
            "MATCH (s:Sound)  WITH words, graphemes, count(s)  AS sounds\n"
            "MATCH (p:Phoneme) WITH words, graphemes, sounds, count(p) AS phonemes\n"
            "MATCH (sk:Skill)\n"
            "RETURN words, graphemes, sounds, phonemes, count(sk) AS skills"
        ),
        tip="Returns a table (scalars). The next queries return graphs.",
    ),
    BrowserQuery(
        key="curriculum",
        title="The curriculum prerequisite DAG",
        why=(
            "The phonics scope & sequence as a graph: 42 Skill nodes, 28 "
            "PREREQUISITE_OF edges. A composite skill requires its component "
            "letters — 'sh' needs 's' and 'h'."
        ),
        cypher="MATCH p=(:Skill)-[:PREREQUISITE_OF]->(:Skill)\nRETURN p",
        tip="Click a node → in the right panel set the caption to `key`.",
    ),
    BrowserQuery(
        key="prereqs",
        title="What a composite skill depends on",
        why="The digraphs/teams that gate a learner's progress, with their prerequisites.",
        cypher=(
            "MATCH (pre:Skill)-[:PREREQUISITE_OF]->(s:Skill)\n"
            "WHERE s.key IN ['sh','ch','th','ck','ai','oa','ar','ee']\n"
            "RETURN pre, s"
        ),
    ),
    BrowserQuery(
        key="mastery",
        title="A learner's mastery over the curriculum",
        why=(
            "The learner overlay: which Skill nodes Ava has mastered. Swap 'ava' "
            "for 'ben', 'maya', or 'cara'."
        ),
        cypher=(
            "MATCH (l:Learner {id: 'ava'})-[m:MASTERED {mastered: true}]->(s:Skill)\nRETURN l, m, s"
        ),
        tip="Color the MASTERED edge or Skill nodes via the right-hand style panel.",
    ),
    BrowserQuery(
        key="frontier",
        title="The ZPD frontier — mastered vs. the next reachable skill",
        why=(
            "Ava's mastered skills plus the prerequisite edges pointing at what they "
            "unlock next. The gap between green and grey is the zone of proximal "
            "development."
        ),
        cypher=(
            "MATCH (l:Learner {id: 'ava'})\n"
            "MATCH (pre:Skill)-[e:PREREQUISITE_OF]->(s:Skill)\n"
            "WHERE (l)-[:MASTERED {mastered: true}]->(pre)\n"
            "RETURN pre, e, s"
        ),
    ),
    BrowserQuery(
        key="reuse",
        title="Shared sub-word units — the reuse made literal",
        why=(
            "A handful of words decomposed into graphemes. The shared nodes (ck, i, "
            "c, k…) visibly connect multiple words — singletons by MERGE-on-key."
        ),
        cypher=(
            "MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)\n"
            "WHERE w.text IN ['kick','sock','pick','sick','tick','tack','pack']\n"
            "RETURN w, g"
        ),
    ),
    BrowserQuery(
        key="decompose",
        title="One word, fully decomposed",
        why=(
            "Every layer the corpus carries for a single word: graphemes, the sounds "
            "they produce, and ARPABET phonemes."
        ),
        cypher=(
            "MATCH (w:Word {text: 'ship'})\n"
            "OPTIONAL MATCH (w)-[:HAS_GRAPHEME]->(g:Grapheme)\n"
            "OPTIONAL MATCH (g)-[:PRODUCES_SOUND]->(s:Sound)\n"
            "OPTIONAL MATCH (w)-[:HAS_PHONEME]->(p:Phoneme)\n"
            "RETURN w, g, s, p"
        ),
        tip="Swap 'ship' for any word in the corpus.",
    ),
    BrowserQuery(
        key="ripple",
        title="The ripple — one new skill unlocks a wave of words",
        why=(
            "Words that become fully decodable for Ava the moment she learns 'ck': "
            "every other grapheme is already mastered. This is step 4 of the app."
        ),
        cypher=(
            "MATCH (l:Learner {id: 'ava'}), (t:Grapheme {key: 'ck'})\n"
            "MATCH (w:Word)-[:HAS_GRAPHEME]->(t)\n"
            "WHERE w.common = true AND w.text =~ '[a-z]{3,}'\n"
            "  AND NOT EXISTS {\n"
            "    MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)\n"
            "    WHERE o.key <> 'ck' AND NOT (l)-[:MASTERED {mastered: true}]->(o)\n"
            "  }\n"
            "RETURN t, w"
        ),
    ),
    BrowserQuery(
        key="rhymes",
        title="Rhyme families — shared Rime nodes",
        why=(
            "Rhyme is modeled as a shared Rime node, not pairwise edges. Words on the "
            "same Rime rhyme by sound (kick/sick/tick), not spelling."
        ),
        cypher=(
            "MATCH (w:Word {text: 'kick'})-[:HAS_RIME]->(r:Rime)<-[:HAS_RIME]-(o:Word)\n"
            "WHERE o.common = true\n"
            "RETURN w, r, o"
        ),
    ),
    BrowserQuery(
        key="minimal-pairs",
        title="Minimal pairs — one phoneme apart",
        why=(
            "Words differing by exactly one phoneme — discrimination practice, derived "
            "at ingest via phoneme-wildcard hashing."
        ),
        cypher=(
            "MATCH (w:Word {text: 'cat'})-[m:MINIMAL_PAIR_OF]-(o:Word)\n"
            "WHERE o.common = true\nRETURN w, m, o"
        ),
    ),
    BrowserQuery(
        key="hub-graphemes",
        title="The busiest shared nodes",
        why=(
            "The graphemes reused across the most words — the hubs that make the graph "
            "small relative to the corpus."
        ),
        cypher=(
            "MATCH (g:Grapheme)<-[:HAS_GRAPHEME]-(w:Word)\n"
            "WITH g, count(w) AS uses\n"
            "RETURN g.text AS grapheme, g.type AS type, uses\n"
            "ORDER BY uses DESC LIMIT 15"
        ),
        tip="Returns a table — the reuse counts behind the 'scale' query.",
    ),
]


def render_text(queries: list[BrowserQuery] | None = None) -> str:
    """Render the queries as a terminal-friendly, copy-pasteable block."""
    qs = queries or BROWSER_QUERIES
    out: list[str] = [
        "Neo4j Browser — open http://localhost:7474 and paste any query below.",
        "Log in with NEO4J_USER / NEO4J_PASSWORD from your .env.",
        "",
    ]
    for i, q in enumerate(qs, 1):
        out.append(f"{'═' * 70}")
        out.append(f"{i}. {q.title}   [{q.key}]")
        out.append(f"   {q.why}")
        out.append("")
        out.extend(f"   {line}" for line in q.cypher.splitlines())
        out.append("")
        if q.tip:
            out.append(f"   💡 {q.tip}")
            out.append("")
    return "\n".join(out)


def render_markdown(queries: list[BrowserQuery] | None = None) -> str:
    """Render the queries as ``docs/neo4j-queries.md`` content."""
    qs = queries or BROWSER_QUERIES
    out: list[str] = [
        "# Exploring rb-neo in Neo4j Browser",
        "",
        "> **Generated** from `rb_neo.browser` — do not edit by hand. "
        "Regenerate with `uv run rb-neo browser-queries --markdown > docs/neo4j-queries.md`.",
        "",
        "Neo4j Browser ships with the database. With the stack running "
        "(`docker compose up -d`), open <http://localhost:7474> and log in with the "
        "`NEO4J_USER` / `NEO4J_PASSWORD` from your `.env` (defaults: `neo4j` / `rbneopass`).",
        "",
        "Paste any query below into the bar at the top. Queries that `RETURN` whole "
        "nodes/paths render as an interactive graph; queries that return scalars render "
        "as a table. Use the style panel (click a node, or the gear at bottom-left) to "
        "set captions and colors.",
        "",
        "## Contents",
        "",
    ]
    for i, q in enumerate(qs, 1):
        anchor = q.title.lower().replace(" — ", "-").replace(" ", "-")
        anchor = "".join(c for c in anchor if c.isalnum() or c == "-")
        out.append(f"{i}. [{q.title}](#{anchor})")
    out.append("")
    for q in qs:
        out.append(f"## {q.title}")
        out.append("")
        out.append(q.why)
        out.append("")
        out.append("```cypher")
        out.append(q.cypher)
        out.append("```")
        out.append("")
        if q.tip:
            out.append(f"> 💡 {q.tip}")
            out.append("")
    return "\n".join(out)
