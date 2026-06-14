"""Animated replay of the ZPD traversal on the real subgraph (vis.js).

Cypher does not emit its intermediate hops, but every stage of the ZPD query
has a real, queryable result. This module pulls those stage results and bakes
them into a self-contained HTML animation: the learner's mastery washes over
the curriculum grid, the prerequisite traversal lights the ZPD gold, leverage
scores appear, the winning skill fans out to its candidate words, and the
i+1 rule accepts/rejects each word on screen. Every frame is driven by data
returned from Neo4j — the animation is a replay of the decision, not a cartoon.

Embed the returned HTML with ``streamlit.components.v1.html``.
"""

from __future__ import annotations

import json

from .db import Neo4jDB
from .recommend import skill_edges, skill_map, zpd_pool
from .traverse import RIPPLE, TOO_HARD, WORD_BREAKDOWN

# Palette (matches traverse.py / the rest of the showcase).
C_LEARNER = "#1f3a5f"
C_MASTERED = "#2e8b57"
C_ZPD = "#e8a33d"
C_LOCKED = "#e9e9e9"
C_TARGET = "#d9534f"
C_IDLE_BG = "#eceff3"
C_IDLE_FG = "#9aa5b1"
C_WORD_OK = "#2e8b57"
C_WORD_BAD = "#d9534f"

_COL_W = 110  # grid spacing, px
_ROW_H = 72


def _grid_positions(rows: list[dict]) -> dict[str, tuple[int, int]]:
    """Skill key -> (x, y): teaching groups as columns, same layout as the DAG."""
    pos: dict[str, tuple[int, int]] = {}
    prev_phase, col, row, count = None, -1, 0, 0
    for r in sorted(rows, key=lambda r: r["seq"]):
        if r["phase"] != prev_phase or count >= 4:
            col, row, count, prev_phase = col + 1, 0, 0, r["phase"]
        pos[r["skill"]] = (col * _COL_W, row * _ROW_H)
        row += 1
        count += 1
    return pos


def _word_breakdown(db: Neo4jDB, learner_id: str, words: list[str]) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for r in db.query(WORD_BREAKDOWN, learner_id=learner_id, words=words):
        out.setdefault(r["word"], []).append((r["grapheme"], bool(r["mastered"])))
    return out


def build_traversal_html(
    db: Neo4jDB,
    learner_id: str,
    learner_name: str,
    emoji: str = "🧒",
    height: int = 640,
    max_words: int = 14,
    max_hard: int = 4,
) -> str:
    """Build the self-contained animated traversal for one learner.

    All stage data comes from the same queries the recommender runs; the
    animation replays them in order with real row counts in the captions.
    """
    smap = skill_map(db, learner_id)
    edges = skill_edges(db)
    pool = zpd_pool(db, learner_id, limit=8)
    target = pool[0]["skill"] if pool else None
    unlocked = (
        [r["word"] for r in db.query(RIPPLE, learner_id=learner_id, target=target)][:max_words]
        if target
        else []
    )
    hard = [r["word"] for r in db.query(TOO_HARD, learner_id=learner_id)[:max_hard]]
    breakdown = _word_breakdown(db, learner_id, unlocked + hard)

    status = {r["skill"]: r["status"] for r in smap}
    unlocks = {r["skill"]: r["unlocks"] for r in pool}
    mastered = [
        r["skill"] for r in sorted(smap, key=lambda r: r["seq"]) if r["status"] == "mastered"
    ]
    zpd = [r["skill"] for r in smap if r["status"] == "zpd"]
    locked = [r["skill"] for r in smap if r["status"] == "locked"]
    pos = _grid_positions(smap)
    grid_w = max(x for x, _ in pos.values())

    # ── nodes ────────────────────────────────────────────────────────────────
    nodes: list[dict] = []
    idle = {"background": C_IDLE_BG, "border": "#c9d1d9"}
    for r in smap:
        x, y = pos[r["skill"]]
        nodes.append(
            {
                "id": f"s:{r['skill']}",
                "label": r["skill"],
                "x": x,
                "y": y,
                "fixed": True,
                "shape": "box",
                "color": dict(idle),
                "font": {"face": "monospace", "size": 18, "color": C_IDLE_FG},
                "margin": 8,
            }
        )
    nodes.append(
        {
            "id": "L",
            "label": f"{emoji} {learner_name}",
            "x": -220,
            "y": int(1.5 * _ROW_H),
            "fixed": True,
            "shape": "box",
            "color": {"background": C_LEARNER, "border": C_LEARNER},
            "font": {"size": 20, "color": "white"},
            "margin": 12,
            "borderWidth": 2,
        }
    )
    word_x0 = grid_w + 220
    all_words = unlocked + hard
    per_col = max(1, (len(all_words) + 1) // 2)
    for i, w in enumerate(all_words):
        nodes.append(
            {
                "id": f"w:{w}",
                "label": w,
                "x": word_x0 + (i // per_col) * 130,
                "y": (i % per_col) * 52 - 40,
                "fixed": True,
                "shape": "box",
                "hidden": True,
                "color": dict(idle),
                "font": {"face": "monospace", "size": 17, "color": "#444"},
                "margin": 7,
            }
        )

    # ── edges ────────────────────────────────────────────────────────────────
    vedges: list[dict] = []
    for s in mastered:
        vedges.append(
            {
                "id": f"m:{s}",
                "from": "L",
                "to": f"s:{s}",
                "hidden": True,
                "color": {"color": "rgba(46,139,87,0.30)"},
                "width": 1,
                "smooth": {"type": "continuous"},
                "arrows": "to",
            }
        )
    for e in edges:
        vedges.append(
            {
                "id": f"p:{e['prereq']}->{e['skill']}",
                "from": f"s:{e['prereq']}",
                "to": f"s:{e['skill']}",
                "hidden": True,
                "color": {"color": "#9aa5b1"},
                "width": 1.5,
                "smooth": {"type": "curvedCW", "roundness": 0.18},
                "arrows": "to",
            }
        )
    for w in all_words:
        for gkey, _m in breakdown.get(w, []):
            if f"s:{gkey}" in {n["id"] for n in nodes}:
                vedges.append(
                    {
                        "id": f"wg:{w}:{gkey}",
                        "from": f"w:{w}",
                        "to": f"s:{gkey}",
                        "hidden": True,
                        "color": {"color": "rgba(31,58,95,0.35)"},
                        "width": 1,
                        "smooth": {"type": "continuous"},
                    }
                )
    edge_ids = {e["id"] for e in vedges}

    # ── stages (each batch of updates = one animation tick) ─────────────────
    def n_upd(nid: str, **kw) -> dict:
        return {"kind": "n", "data": {"id": nid, **kw}}

    def e_upd(eid: str, **kw) -> dict:
        return {"kind": "e", "data": {"id": eid, **kw}}

    stages: list[dict] = []

    # S1 — mastery overlay washes over the grid.
    s1_updates = []
    for s in mastered:
        s1_updates.append(
            [
                e_upd(f"m:{s}", hidden=False),
                n_upd(
                    f"s:{s}",
                    color={"background": C_MASTERED, "border": C_MASTERED},
                    font={"face": "monospace", "size": 18, "color": "white"},
                ),
            ]
        )
    stages.append(
        {
            "caption": f"<b>1 · Mastery overlay.</b> BKT posteriors from {learner_name}'s "
            f"attempt history → <b>{len(mastered)} skills mastered</b> (green edges = "
            "<code>MASTERED</code> relationships).",
            "ticker": f"{len(mastered)} MASTERED edges",
            "batches": s1_updates,
            "batchDelay": 90,
            "pause": 1100,
        }
    )

    # S2 — prerequisite traversal: ZPD gold, locked grey with red blockers.
    s2_first = [e_upd(f"m:{s}", hidden=True) for s in mastered]
    s2_batches = [s2_first]
    for s in zpd:
        batch = [
            n_upd(
                f"s:{s}",
                color={"background": C_ZPD, "border": "#8a5a00"},
                font={"face": "monospace", "size": 18, "color": "black"},
            )
        ]
        for e in edges:
            eid = f"p:{e['prereq']}->{e['skill']}"
            if e["skill"] == s and eid in edge_ids:
                batch.append(e_upd(eid, hidden=False, color={"color": C_MASTERED}, width=2))
        s2_batches.append(batch)
    for s in locked:
        batch = []
        for e in edges:
            eid = f"p:{e['prereq']}->{e['skill']}"
            if e["skill"] == s and status.get(e["prereq"]) != "mastered" and eid in edge_ids:
                batch.append(e_upd(eid, hidden=False, color={"color": C_TARGET}, width=2))
        if batch:
            s2_batches.append(batch)
    stages.append(
        {
            "caption": "<b>2 · Prerequisite traversal.</b> A skill joins the <b>ZPD</b> (gold) "
            "only when every <code>PREREQUISITE_OF</code> arrow into it comes from a green "
            f"skill. <b>{len(zpd)} skills in the ZPD</b>; red arrows show what locks the rest.",
            "ticker": f"{len(zpd)} in ZPD · {len(locked)} locked",
            "batches": s2_batches,
            "batchDelay": 160,
            "pause": 1400,
        }
    )

    # S3 — leverage scoring on the gold pool.
    s3_batches = [[n_upd(f"s:{r['skill']}", label=f"{r['skill']}  +{r['unlocks']}")] for r in pool]
    stages.append(
        {
            "caption": "<b>3 · Leverage scoring.</b> For each gold skill the graph counts the "
            "words that would become fully decodable the moment it is learned — one "
            "<code>COUNT</code> subquery over shared grapheme nodes.",
            "ticker": " · ".join(f"{r['skill']} +{r['unlocks']}" for r in pool[:5]),
            "batches": s3_batches,
            "batchDelay": 220,
            "pause": 1400,
        }
    )

    if target:
        # S4 — winner flares; its words fan out with their grapheme edges. The
        # stage-2 prerequisite edges fade out so the word structure reads clean.
        s4_first = [
            n_upd(
                f"s:{target}",
                color={"background": C_TARGET, "border": "#7f1d1d"},
                font={"face": "monospace", "size": 20, "color": "white"},
                borderWidth=3,
            )
        ]
        for e in edges:
            eid = f"p:{e['prereq']}->{e['skill']}"
            if eid in edge_ids and e["skill"] != target:
                s4_first.append(e_upd(eid, color={"color": "rgba(154,165,177,0.12)"}, width=1))
        s4_batches = [s4_first]
        for w in unlocked:
            batch = [n_upd(f"w:{w}", hidden=False)]
            for gkey, _m in breakdown.get(w, []):
                eid = f"wg:{w}:{gkey}"
                if eid in edge_ids:
                    batch.append(e_upd(eid, hidden=False))
            s4_batches.append(batch)
        stages.append(
            {
                "caption": f"<b>4 · Winner: <code>{target}</code></b> "
                f"(+{unlocks.get(target, 0)} words). Its candidate words fan out — each edge "
                "is a shared <code>HAS_GRAPHEME</code> link back into the curriculum grid.",
                "ticker": f"target = {target}",
                "batches": s4_batches,
                "batchDelay": 170,
                "pause": 1200,
                "fit": True,
            }
        )

        # S5 — the i+1 rule fires per word: accept green, reject red.
        s5_batches = []
        for w in unlocked:
            s5_batches.append(
                [
                    n_upd(
                        f"w:{w}",
                        color={"background": C_WORD_OK, "border": C_WORD_OK},
                        font={"face": "monospace", "size": 17, "color": "white"},
                    )
                ]
            )
        for w in hard:
            batch = [
                n_upd(
                    f"w:{w}",
                    hidden=False,
                    color={"background": "#fdecea", "border": C_WORD_BAD},
                    font={"face": "monospace", "size": 17, "color": C_WORD_BAD},
                )
            ]
            for gkey, _m in breakdown.get(w, []):
                eid = f"wg:{w}:{gkey}"
                if eid in edge_ids:
                    batch.append(e_upd(eid, hidden=False, color={"color": "rgba(217,83,79,0.35)"}))
            s5_batches.append(batch)
        stages.append(
            {
                "caption": "<b>5 · The i+1 rule fires.</b> A word is accepted (green) only when "
                f"<code>{target}</code> is its <i>single</i> non-mastered grapheme. Words with "
                "two or more unknowns are rejected (red) — out of the ZPD.",
                "ticker": f"{len(unlocked)} accepted · {len(hard)} rejected",
                "batches": s5_batches,
                "batchDelay": 200,
                "pause": 1300,
            }
        )

        stages.append(
            {
                "caption": f"<b>6 · Output.</b> A ranked, graph-guaranteed word list for "
                f"<code>{target}</code> — handed to Claude as the <b>safe set</b> it must build "
                f"{learner_name}'s lesson from. Deterministic routing; AI only personalizes.",
                "ticker": f"{len(unlocked)} safe words → Claude",
                "batches": [[]],
                "batchDelay": 100,
                "pause": 600,
            }
        )

    payload = {
        "nodes": nodes,
        "edges": vedges,
        "stages": stages,
        "height": height,
    }
    return _HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<script src="https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
  body { margin: 0; font-family: -apple-system, "Segoe UI", Helvetica, sans-serif; }
  #caption { min-height: 44px; padding: 8px 12px; font-size: 14.5px; color: #1a1a2e;
             background: #f6f8fa; border-radius: 8px; margin-bottom: 6px; }
  #bar { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  #ticker { font-family: monospace; font-size: 13px; color: #555; }
  #replay { background: #d9534f; color: white; border: none; border-radius: 6px;
            padding: 6px 14px; font-size: 14px; font-weight: 600; cursor: pointer; }
  #replay:hover { background: #b9423e; }
  #net { width: 100%; border: 1px solid #e3e7ec; border-radius: 8px; background: #fcfdfe; }
  code { background: #eef1f4; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
</style>
</head>
<body>
<div id="bar">
  <button id="replay">&#9654;&nbsp;Replay traversal</button>
  <span id="ticker"></span>
</div>
<div id="caption">Press <b>Replay traversal</b> to watch the graph compute the recommendation.</div>
<div id="net"></div>
<script>
const P = __PAYLOAD__;
document.getElementById('net').style.height = P.height + 'px';

const initialNodes = JSON.parse(JSON.stringify(P.nodes));
const initialEdges = JSON.parse(JSON.stringify(P.edges));
const nodes = new vis.DataSet(JSON.parse(JSON.stringify(P.nodes)));
const edges = new vis.DataSet(JSON.parse(JSON.stringify(P.edges)));
const network = new vis.Network(document.getElementById('net'), {nodes, edges}, {
  physics: false,
  interaction: {dragNodes: false, zoomView: true, dragView: true, hover: true},
});
network.once('afterDrawing', () => network.fit({animation: false}));

const captionEl = document.getElementById('caption');
const tickerEl = document.getElementById('ticker');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let runId = 0;

function reset() {
  nodes.update(JSON.parse(JSON.stringify(initialNodes)));
  edges.update(JSON.parse(JSON.stringify(initialEdges)));
}

// Fit to ALL payload nodes (vis's own fit() skips hidden ones, so the word
// column that appears in stage 4 would otherwise land off-screen).
function fitAll() {
  const xs = P.nodes.map(n => n.x), ys = P.nodes.map(n => n.y);
  const minX = Math.min(...xs) - 90, maxX = Math.max(...xs) + 90;
  const minY = Math.min(...ys) - 70, maxY = Math.max(...ys) + 70;
  const c = document.getElementById('net');
  const scale = Math.min(c.clientWidth / (maxX - minX), c.clientHeight / (maxY - minY));
  network.moveTo({
    position: {x: (minX + maxX) / 2, y: (minY + maxY) / 2},
    scale: scale,
    animation: {duration: 700, easingFunction: 'easeInOutQuad'},
  });
}

async function play() {
  const my = ++runId;
  reset();
  for (const st of P.stages) {
    if (my !== runId) return;
    captionEl.innerHTML = st.caption;
    if (st.ticker !== undefined) tickerEl.textContent = st.ticker;
    if (st.fit) fitAll();
    for (const batch of st.batches) {
      if (my !== runId) return;
      for (const u of batch) {
        if (u.kind === 'n') nodes.update(u.data); else edges.update(u.data);
      }
      await sleep(st.batchDelay || 130);
    }
    await sleep(st.pause || 1000);
  }
}

document.getElementById('replay').addEventListener('click', play);
setTimeout(play, 600);   // auto-play once on load
</script>
</body>
</html>
"""
