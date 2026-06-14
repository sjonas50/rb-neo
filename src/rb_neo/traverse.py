"""Step-by-step traversal narration for the showcase.

Each function returns BOTH the Cypher it ran and the result, so the UI can show
the query *and* the graph it produced — the recommendation visibly emerging from
a traversal rather than appearing as an opaque list.

The narrative is the research-validated ZPD loop (docs/research.md §Layer 1-2):
  1. the child's position on the curriculum DAG (mastered / ZPD / locked),
  2. the ZPD pool ranked by leverage — the graph picks the next skill,
  3. i+1 word selection — exactly one new grapheme per word,
  4. the ripple — one skill unlocks a wave of words, plus the rhyme-family and
     minimal-pair structure the graph already knows about them.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field

from .db import Neo4jDB
from .recommend import (
    _CROSS_WORD,
    _ZPD_POOL,
    locked_skills,
    skill_edges,
    skill_map,
    zpd_pool,
)

# ── colors ──────────────────────────────────────────────────────────────────
C_MASTERED = "#2e8b57"  # green  — already known
C_ZPD = "#e8a33d"  # gold   — in the ZPD: unmastered, prerequisites met
C_LOCKED = "#e9e9e9"  # grey   — blocked by an unmastered prerequisite
C_TARGET = "#d9534f"  # red    — the one new grapheme being introduced
C_WORD_OK = "#eafaef"


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

# Per-word letter breakdown with mastery flags — feeds the color-coded chips.
WORD_BREAKDOWN = """
MATCH (l:Learner {id: $learner_id})
UNWIND $words AS wt
MATCH (w:Word {text: wt})-[r:HAS_GRAPHEME]->(g:Grapheme)
OPTIONAL MATCH (l)-[m:MASTERED {mastered: true}]->(g)
RETURN wt AS word, r.pos AS pos, g.key AS grapheme, (m IS NOT NULL) AS mastered
ORDER BY wt, r.pos
"""

# Words the learner cannot yet decode (2+ unknown grapheme keys) — "too hard".
TOO_HARD = """
MATCH (l:Learner {id: $learner_id})
OPTIONAL MATCH (l)-[:MASTERED {mastered: true}]->(mg:Grapheme)
WITH collect(DISTINCT mg.key) AS known
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
WITH w, known, collect(DISTINCT g.key) AS keys
WITH w, [k IN keys WHERE NOT k IN known] AS new
WHERE size(new) >= 2
RETURN w.text AS word, size(new) AS new_count
ORDER BY new_count DESC, w.text
"""

# The ripple: learning ONE grapheme unlocks every word whose only missing piece
# was that grapheme.
RIPPLE = """
MATCH (l:Learner {id: $learner_id})
MATCH (w:Word)-[:HAS_GRAPHEME]->(t:Grapheme)
WHERE t.key = $target AND w.common = true AND w.text =~ '[a-z]{3,}'
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
    WHERE o.key <> $target AND NOT (l)-[:MASTERED {mastered: true}]->(o)
  }
RETURN DISTINCT w.text AS word
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

# Rhyme families among a word set: grouped by shared Rime node (phonological,
# not spelling-based — 'sock' and 'pack' do NOT rhyme; 'kick'/'sick' do).
RIME_GROUPS = """
UNWIND $words AS wt
MATCH (w:Word {text: wt})-[:HAS_RIME]->(r:Rime)
WITH r.key AS rime, collect(DISTINCT w.text) AS members
WHERE size(members) >= 2
RETURN rime, members
ORDER BY size(members) DESC, rime
LIMIT 4
"""

# Minimal pairs (differ by exactly one phoneme) reaching out of the unlocked set
# into other words the child can read — discrimination practice for free.
MINIMAL_PAIRS_OF = """
UNWIND $words AS wt
MATCH (w:Word {text: wt})-[:MINIMAL_PAIR_OF]-(o:Word)
WHERE o.common = true
WITH w.text AS word, collect(DISTINCT o.text) AS all_pairs
RETURN word, all_pairs[..4] AS pairs
ORDER BY size(all_pairs) DESC, word
LIMIT 4
"""


# Funnel: real row counts at each narrowing stage of the word-selection query.
_FUNNEL_TOTAL = "MATCH (w:Word) RETURN count(w) AS n"
_FUNNEL_CURATED = (
    "MATCH (w:Word) WHERE w.common = true AND w.text =~ '[a-z]{3,}' RETURN count(w) AS n"
)
_FUNNEL_HAS_TARGET = """
MATCH (w:Word)-[:HAS_GRAPHEME]->(t:Grapheme)
WHERE t.key = $target AND w.common = true AND w.text =~ '[a-z]{3,}'
RETURN count(DISTINCT w) AS n
"""


def funnel(db: Neo4jDB, learner_id: str, target: str) -> list[dict]:
    """Row counts flowing through the i+1 word query, stage by stage.

    Returns ``[{label, clause, count}]`` — the live evidence that the output
    list is the residue of graph-side narrowing, not app logic.
    """
    total = db.query(_FUNNEL_TOTAL)[0]["n"]
    curated = db.query(_FUNNEL_CURATED)[0]["n"]
    has_target = db.query(_FUNNEL_HAS_TARGET, target=target)[0]["n"]
    accepted = len(db.query(RIPPLE, learner_id=learner_id, target=target))
    return [
        {"label": "Words in the graph", "clause": "MATCH (w:Word)", "count": total},
        {
            "label": "Curated decodable words",
            "clause": "WHERE w.common = true AND w.text =~ '[a-z]{3,}'",
            "count": curated,
        },
        {
            "label": f"Contain the target '{target}'",
            "clause": "MATCH (w)-[:HAS_GRAPHEME]->(t {key: $target})",
            "count": has_target,
        },
        {
            "label": "Every OTHER grapheme already mastered",
            "clause": "NOT EXISTS { (w)-[:HAS_GRAPHEME]->(o) WHERE o unmastered }",
            "count": accepted,
        },
    ]


# ── helpers ──────────────────────────────────────────────────────────────────


def _esc(s: str) -> str:
    return s.replace('"', '\\"')


def _breakdown(db: Neo4jDB, learner_id: str, words: list[str]) -> dict[str, list[tuple[str, bool]]]:
    """word -> ordered [(grapheme_key, mastered)]."""
    out: dict[str, list[tuple[str, bool]]] = collections.defaultdict(list)
    for r in db.query(WORD_BREAKDOWN, learner_id=learner_id, words=words):
        out[r["word"]].append((r["grapheme"], bool(r["mastered"])))
    return out


def _chips(breakdown: dict[str, list[tuple[str, bool]]], words: list[str], target: str) -> list:
    """Per-word colored-letter chip data.

    Returns ``{word, letters: [(display, state)]}`` with state
    ``mastered`` | ``target`` | ``other_new`` — the rule made visible.
    """
    out = []
    for w in words:
        letters = []
        for gkey, mastered in breakdown.get(w, []):
            if gkey == target:
                state = "target"
            elif mastered:
                state = "mastered"
            else:
                state = "other_new"
            letters.append((gkey, state))
        out.append({"word": w, "letters": letters})
    return out


_STATUS_STYLE = {
    "mastered": f'style="filled,rounded", fillcolor="{C_MASTERED}", fontcolor="white"',
    "zpd": f'style="filled,rounded,bold", fillcolor="{C_ZPD}", fontcolor="black", '
    f'color="#8a5a00", penwidth=2',
    "locked": f'style="filled,rounded,dashed", fillcolor="{C_LOCKED}", fontcolor="#888888"',
}


def _dot_skill_map(rows: list[dict], edges: list[dict]) -> str:
    """The curriculum map, colored by this learner's status.

    Nodes are pinned to a deterministic grid (neato layout): teaching groups
    become columns, left → right in scope-sequence order. Only the edges that
    explain *today's frontier* are drawn — red blockers into locked skills and
    the just-satisfied prerequisites into ZPD composites — so the picture stays
    legible (the full DAG has ~28 edges; most are history once mastered).
    """
    lines = [
        "digraph G {",
        "  layout=neato;",
        "  splines=true;",
        '  bgcolor="transparent";',
        '  node [fontname="Helvetica", fontsize=13, shape=box, width=0.52, height=0.34, '
        'fixedsize=true, margin="0.08,0.04"];',
        '  edge [color="#9aa5b1", arrowsize=0.6];',
    ]
    # Columns mirror the curriculum's teaching groups: a new column whenever the
    # phase changes or after 4 skills (group size in `curriculum.PHASES`).
    prev_phase = None
    count_in_col = 0
    col = -1
    row = 0
    for r in sorted(rows, key=lambda r: r["seq"]):
        if r["phase"] != prev_phase or count_in_col >= 4:
            col += 1
            row = 0
            count_in_col = 0
            prev_phase = r["phase"]
        lines.append(
            f'  "{_esc(r["skill"])}" [label="{_esc(r["skill"])}", '
            f'pos="{col * 1.05:.2f},{-row * 0.6:.2f}!", {_STATUS_STYLE[r["status"]]}];'
        )
        row += 1
        count_in_col += 1
    status = {r["skill"]: r["status"] for r in rows}
    for e in edges:
        if status.get(e["skill"]) == "locked" and status.get(e["prereq"]) != "mastered":
            # The arrow doing the locking — make the blocker visible.
            lines.append(
                f'  "{_esc(e["prereq"])}" -> "{_esc(e["skill"])}" '
                f'[color="{C_TARGET}", penwidth=1.6];'
            )
        elif status.get(e["skill"]) == "zpd":
            # Freshly satisfied prerequisites — why this skill just unlocked.
            lines.append(
                f'  "{_esc(e["prereq"])}" -> "{_esc(e["skill"])}" '
                f'[color="{C_MASTERED}", penwidth=1.3];'
            )
    lines.append("}")
    return "\n".join(lines)


def _dot_ripple_tree(target: str, words: list[str]) -> str:
    """One-to-many tree: the new grapheme as a hub, unlocked words fanning out."""
    lines = [
        "digraph G {",
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        '  size="7,4.5";',
        "  ratio=compress;",
        "  ranksep=1.0;",
        "  nodesep=0.12;",
        '  node [fontname="Helvetica", fontsize=13];',
        '  edge [color="#9aa5b1", arrowsize=0.5];',
        f'  "t" [label="{_esc(target)}", shape=circle, style=filled, '
        f'fillcolor="{C_TARGET}", fontcolor="white", fontsize=20, width=0.9, fixedsize=true];',
    ]
    for w in words:
        lines.append(
            f'  "w_{w}" [label="{_esc(w)}", shape=box, style="filled,rounded", '
            f'fillcolor="{C_WORD_OK}", color="{C_MASTERED}", penwidth=2];'
        )
        lines.append(f'  "t" -> "w_{w}";')
    lines.append("}")
    return "\n".join(lines)


# ── steps ────────────────────────────────────────────────────────────────────


def step_skill_map(db: Neo4jDB, learner_id: str, learner_name: str) -> Step:
    """Step 1 — the child's position on the curriculum prerequisite DAG."""
    rows = skill_map(db, learner_id)
    edges = skill_edges(db)
    counts = collections.Counter(r["status"] for r in rows)
    return Step(
        cypher="MATCH (s:Skill) ...status per skill (see ZPD query in step 2)",
        params={"learner_id": learner_id},
        rows=rows,
        dot=_dot_skill_map(rows, edges),
        note=(
            f"The curriculum is a **prerequisite graph**, not a list. {learner_name}: "
            f"🟩 {counts['mastered']} skills mastered · "
            f"🟨 {counts['zpd']} in the ZPD (unmastered, every prerequisite met — "
            f"learnable *right now*) · "
            f"⬜ {counts['locked']} locked (an arrow arrives from an unmastered skill)."
        ),
        extra={"counts": dict(counts), "rows": rows},
    )


def step_zpd_decision(db: Neo4jDB, learner_id: str, learner_name: str) -> Step:
    """Step 2 — the ZPD pool, ranked by how many words each skill unlocks."""
    pool = zpd_pool(db, learner_id, limit=8)
    locked = locked_skills(db, learner_id)
    if not pool:
        return Step(
            cypher=_ZPD_POOL.strip(),
            params={"learner_id": learner_id},
            rows=[],
            note=f"{learner_name} has mastered the whole curriculum — move to fluency.",
        )
    target = pool[0]["skill"]
    return Step(
        cypher=_ZPD_POOL.strip(),
        params={"learner_id": learner_id, "limit": 8},
        rows=pool,
        note=(
            f"One traversal computes {learner_name}'s ZPD **and** scores each skill by "
            f"leverage — how many real words it would unlock today. Winner: **'{target}'** "
            f"(+{pool[0]['unlocks']} words). No rules engine, no LLM: the curriculum DAG, "
            "the mastery overlay, and the word corpus answer together."
        ),
        extra={"target": target, "pool": pool, "locked": locked},
    )


def step_words(
    db: Neo4jDB, learner_id: str, learner_name: str, target: str, n_each: int = 4
) -> Step:
    """Step 3 — i+1 word selection: practice the target with zero other surprises."""
    accepted = [
        r["word"]
        for r in db.query(
            _CROSS_WORD, learner_id=learner_id, target=target, limit=n_each, common_only=True
        )
    ]
    known = [r["word"] for r in db.query(DECODABLE_WORDS, learner_id=learner_id, limit=n_each)]
    hard = [r["word"] for r in db.query(TOO_HARD, learner_id=learner_id)[:n_each]]
    bd = _breakdown(db, learner_id, accepted + known + hard)
    return Step(
        cypher=_CROSS_WORD.strip(),
        params={"learner_id": learner_id, "target": target},
        rows=[{"word": w} for w in accepted],
        note=(
            f"A word qualifies only when **'{target}' is its single new grapheme** — every "
            f"other unit is one {learner_name} has already mastered (i+1: maximal reuse, "
            "one new thing)."
        ),
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
    """Step 4 — one grapheme unlocks a wave; the graph knows its inner structure."""
    before = db.query(DECODABLE_COUNT, learner_id=learner_id)[0]["decodable"]
    unlocked = [r["word"] for r in db.query(RIPPLE, learner_id=learner_id, target=target)]
    rimes = db.query(RIME_GROUPS, words=unlocked)
    pairs = db.query(MINIMAL_PAIRS_OF, words=unlocked)
    return Step(
        cypher=RIPPLE.strip(),
        params={"learner_id": learner_id, "target": target},
        rows=[{"word": w} for w in unlocked],
        dot=_dot_ripple_tree(target, unlocked[:12]),
        note=(
            f"One new grapheme — '{target}' — and **{len(unlocked)} words** become decodable "
            f"for {learner_name} in a single traversal. That network effect is why the "
            "content lives in a graph."
        ),
        extra={
            "target": target,
            "unlocked": unlocked,
            "before": before,
            "after": before + len(unlocked),
            "rimes": rimes,
            "pairs": pairs,
        },
    )
