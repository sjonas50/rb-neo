"""Step-by-step traversal narration for the showcase.

Each function returns BOTH the Cypher it ran and the result, so the UI can show
the query *and* the graph it produced — the recommendation visibly emerging from
a traversal rather than appearing as an opaque list. Graphviz DOT builders render
the same data as a legible, deterministic graph (color = the pedagogical verdict).
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .db import Neo4jDB

# ── colors ──────────────────────────────────────────────────────────────────
C_LEARNER = "#1f3a5f"
C_MASTERED = "#2e8b57"  # green  — already known
C_TARGET = "#d9534f"  # red    — the one new grapheme being introduced
C_OTHER_NEW = "#e0a458"  # orange — another not-yet-known grapheme
C_WORD_OK = "#eafaef"
C_WORD_KNOWN = "#eeeeee"
C_WORD_HARD = "#fdecea"


@dataclass
class Step:
    """One narrated traversal step."""

    cypher: str
    params: dict
    rows: list[dict]
    dot: str = ""
    note: str = ""
    extra: dict = field(default_factory=dict)


# ── Cypher (kept as named constants so the UI can display them verbatim) ──────

LEARNER_STATE = """
MATCH (l:Learner {id: $learner_id})-[:MASTERED {mastered: true}]->(g:Grapheme)
RETURN g.text AS grapheme, g.type AS type
ORDER BY g.text
"""

# The decision query: a word is "next-best" when every grapheme is mastered
# except exactly one — maximal reuse, one new thing to learn.
NEXT_BEST = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
WITH l, w, collect(DISTINCT g) AS gs,
     [x IN collect(DISTINCT g) WHERE NOT (l)-[:MASTERED {mastered: true}]->(x)] AS new
WHERE size(new) = 1
RETURN w.text AS word, new[0].text AS introduces
ORDER BY size(gs), w.text
"""

# Per-word letter breakdown with mastery flags — feeds the color-coded graph.
WORD_BREAKDOWN = """
MATCH (l:Learner {id: $learner_id})
UNWIND $words AS wt
MATCH (w:Word {text: wt})-[r:HAS_GRAPHEME]->(g:Grapheme)
OPTIONAL MATCH (l)-[m:MASTERED {mastered: true}]->(g)
RETURN wt AS word, r.pos AS pos, g.text AS grapheme, (m IS NOT NULL) AS mastered
ORDER BY wt, r.pos
"""

# Words the learner cannot yet decode (2+ unknown graphemes) — the "too hard" set.
TOO_HARD = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
  AND NOT (l)-[:MASTERED {mastered: true}]->(g)
WITH w, count(DISTINCT g) AS new_count
WHERE new_count >= 2
RETURN w.text AS word, new_count
ORDER BY new_count DESC, w.text
"""

# The ripple: learning ONE grapheme unlocks every word whose only missing piece
# was that grapheme. (Same shape as the recommender's cross-word query.)
RIPPLE = """
MATCH (l:Learner {id: $learner_id}), (t:Grapheme {text: $target})
MATCH (w:Word)-[:HAS_GRAPHEME]->(t)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
    WHERE o.text <> $target AND NOT (l)-[:MASTERED {mastered: true}]->(o)
  }
RETURN w.text AS word
ORDER BY size(w.text), w.text
"""

DECODABLE_COUNT = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(g:Grapheme)
    WHERE NOT (l)-[:MASTERED {mastered: true}]->(g)
  }
RETURN count(w) AS decodable
"""

DECODABLE_WORDS = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(g:Grapheme)
    WHERE NOT (l)-[:MASTERED {mastered: true}]->(g)
  }
RETURN w.text AS word
ORDER BY size(w.text), w.text
LIMIT $limit
"""


# ── helpers ──────────────────────────────────────────────────────────────────


def _breakdown(db: Neo4jDB, learner_id: str, words: list[str]) -> dict[str, list[tuple[str, bool]]]:
    """word -> ordered [(grapheme, mastered)]."""
    out: dict[str, list[tuple[str, bool]]] = collections.defaultdict(list)
    for r in db.query(WORD_BREAKDOWN, learner_id=learner_id, words=words):
        out[r["word"]].append((r["grapheme"], bool(r["mastered"])))
    return out


def _esc(s: str) -> str:
    return s.replace('"', '\\"')


def _dot_ripple_tree(target: str, words: list[str]) -> str:
    """A clean one-to-many tree: the new grapheme as a hub, unlocked words fanning out.

    One-to-many means no edge crossing — it reads as "this one skill unlocked all these".
    """
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        "  ranksep=1.2;",
        '  node [fontname="Helvetica", fontsize=14];',
        '  edge [color="#9aa5b1", arrowsize=0.6];',
        f'  "t" [label="{_esc(target.lower())}", shape=circle, style=filled, '
        f'fillcolor="{C_TARGET}", fontcolor="white", fontsize=22, width=1.0, fixedsize=true];',
    ]
    for w in words:
        lines.append(
            f'  "w_{w}" [label="{_esc(w)}", shape=box, style="filled,rounded", '
            f'fillcolor="{C_WORD_OK}", color="{C_MASTERED}", penwidth=2];'
        )
        lines.append(f'  "t" -> "w_{w}";')
    lines.append("}")
    return "\n".join(lines)


def _chips(breakdown: dict[str, list[tuple[str, bool]]], words: list[str], target: str) -> list:
    """Per-word colored-letter chip data for the decision step.

    Returns a list of ``{word, letters: [(display, state)]}`` where state is
    ``mastered`` | ``target`` | ``other_new`` — the basis for the legible,
    rule-obvious rendering of "exactly one new letter".
    """
    out = []
    for w in words:
        letters = []
        for gtext, mastered in breakdown.get(w, []):
            if gtext == target:
                state = "target"
            elif mastered:
                state = "mastered"
            else:
                state = "other_new"
            letters.append((gtext.lower(), state))
        out.append({"word": w, "letters": letters})
    return out


def _dot_learner_state(learner_name: str, graphemes: list[str]) -> str:
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        '  node [fontname="Helvetica", fontsize=14];',
        '  edge [color="#cccccc", arrowsize=0.5];',
        f'  "L" [label="{_esc(learner_name)}", shape=box, style="filled,rounded", '
        f'fillcolor="{C_LEARNER}", fontcolor="white"];',
    ]
    for g in graphemes:
        lines.append(
            f'  "g_{g}" [label="{_esc(g.lower())}", shape=circle, style=filled, '
            f'fillcolor="{C_MASTERED}", fontcolor="white"];'
        )
        lines.append(f'  "L" -> "g_{g}";')
    lines.append("}")
    return "\n".join(lines)


# ── steps ────────────────────────────────────────────────────────────────────


def step_learner_state(db: Neo4jDB, learner_id: str, learner_name: str) -> Step:
    """Step 1 — what the graph knows about this child."""
    rows = db.query(LEARNER_STATE, learner_id=learner_id)
    gs = [r["grapheme"] for r in rows]
    return Step(
        cypher=LEARNER_STATE.strip(),
        params={"learner_id": learner_id},
        rows=rows,
        dot=_dot_learner_state(learner_name, gs),
        note=f"{learner_name} has mastered {len(gs)} graphemes (green). "
        "Everything that follows is a traversal from this set.",
        extra={"mastered": gs},
    )


def step_decision(db: Neo4jDB, learner_id: str, learner_name: str, n_each: int = 4) -> Step:
    """Step 2 — the graph evaluates candidates and picks the next skill."""
    nbw = db.query(NEXT_BEST, learner_id=learner_id)
    if not nbw:
        return Step(
            cypher=NEXT_BEST.strip(),
            params={"learner_id": learner_id},
            rows=[],
            note=f"{learner_name} can already decode every common word — move to fluency.",
        )
    target = collections.Counter(r["introduces"] for r in nbw).most_common(1)[0][0]

    accepted = [r["word"] for r in nbw if r["introduces"] == target][:n_each]
    known = [r["word"] for r in db.query(DECODABLE_WORDS, learner_id=learner_id, limit=n_each)]
    hard = [r["word"] for r in db.query(TOO_HARD, learner_id=learner_id)[:n_each]]

    bd = _breakdown(db, learner_id, accepted + known + hard)
    return Step(
        cypher=NEXT_BEST.strip(),
        params={"learner_id": learner_id},
        rows=nbw,
        note=f"The rule is visible per word: a word is **next-best** only when exactly "
        f"one letter is still red. The highest-leverage new skill here is **'{target}'**.",
        extra={
            "target": target,
            "accepted": accepted,
            "known": known,
            "hard": hard,
            "chips_accepted": _chips(bd, accepted, target),
            "chips_known": _chips(bd, known, target),
            "chips_hard": _chips(bd, hard, target),
        },
    )


def step_ripple(db: Neo4jDB, learner_id: str, learner_name: str, target: str) -> Step:
    """Step 3 — learning one grapheme unlocks a wave of newly-decodable words."""
    before = db.query(DECODABLE_COUNT, learner_id=learner_id)[0]["decodable"]
    unlocked = [r["word"] for r in db.query(RIPPLE, learner_id=learner_id, target=target)]
    return Step(
        cypher=RIPPLE.strip(),
        params={"learner_id": learner_id, "target": target},
        rows=[{"word": w} for w in unlocked],
        dot=_dot_ripple_tree(target, unlocked[:14]),
        note=f"One new grapheme — '{target}' — unlocks **{len(unlocked)} common words** in a "
        "single traversal. That network effect is why a graph beats flat storage.",
        extra={
            "target": target,
            "unlocked": unlocked,
            "before": before,
            "after": before + len(unlocked),
        },
    )
