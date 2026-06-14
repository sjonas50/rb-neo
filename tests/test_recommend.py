"""Integration tests for the ZPD recommender against a seeded graph.

These require Neo4j and the word corpus; they skip cleanly otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rb_neo import recommend
from rb_neo.ingest import ensure_common_words, load_from_dir
from rb_neo.synthetic import seed_learners

from .conftest import requires_neo4j

WORDS_DIR = Path(__file__).resolve().parents[1] / "words"

pytestmark = pytest.mark.skipif(
    not (WORDS_DIR / "cat.json").exists(), reason="words corpus not present"
)


@pytest.fixture
def seeded(db):
    """Ingest a slice of the corpus and seed synthetic learners."""
    load_from_dir(db, WORDS_DIR, limit=4000)
    ensure_common_words(db, WORDS_DIR)
    seed_learners(db)
    return db


@requires_neo4j
def test_next_best_word_introduces_exactly_one_new_grapheme(seeded) -> None:
    db = seeded
    rows = recommend.next_best_word(db, "ava", limit=20)
    assert rows, "expected at least one next-best word"
    for r in rows:
        # Every returned word's unmastered key set is exactly the 'introduces' grapheme.
        masked = db.query(
            "MATCH (l:Learner {id:'ava'}), (w:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme) "
            "WHERE NOT (l)-[:MASTERED {mastered:true}]->(g) "
            "RETURN collect(DISTINCT g.key) AS unmastered",
            wt=r["word"],
        )[0]["unmastered"]
        assert masked == [r["introduces"]]


@requires_neo4j
def test_mastery_aware_set_is_fully_decodable(seeded) -> None:
    db = seeded
    rows = recommend.mastery_aware(db, "ben", limit=20)
    for r in rows:
        unmastered = db.query(
            "MATCH (l:Learner {id:'ben'}), (w:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme) "
            "WHERE NOT (l)-[:MASTERED {mastered:true}]->(g) "
            "RETURN count(g) AS n",
            wt=r["word"],
        )[0]["n"]
        assert unmastered == 0


@requires_neo4j
def test_cross_word_contains_target(seeded) -> None:
    db = seeded
    rows = recommend.cross_word(db, "cara", "sh", limit=10)
    for r in rows:
        has_target = db.query(
            "MATCH (w:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme {key:'sh'}) "
            "RETURN count(g) AS n",
            wt=r["word"],
        )[0]["n"]
        assert has_target >= 1


@requires_neo4j
def test_advanced_learner_knows_more_than_beginner(seeded) -> None:
    db = seeded
    ava = recommend.mastery_summary(db, "ava")
    cara = recommend.mastery_summary(db, "cara")
    assert cara["mastered"] > ava["mastered"]


@requires_neo4j
def test_personas_present(seeded) -> None:
    db = seeded
    learners = {x["id"]: x for x in recommend.list_learners(db)}
    assert {"ava", "ben", "maya", "cara"} <= set(learners)
    assert "dinosaurs" in learners["ava"]["interests"]


@requires_neo4j
def test_mastery_twins_have_identical_safe_set(seeded) -> None:
    # Ben and Maya share mastery on purpose -> identical decodable set (the split-screen).
    db = seeded
    ben = [r["word"] for r in recommend.cross_word(db, "ben", "sh", limit=10)]
    maya = [r["word"] for r in recommend.cross_word(db, "maya", "sh", limit=10)]
    assert ben and ben == maya


@requires_neo4j
def test_common_only_restricts_to_curated_words(seeded) -> None:
    db = seeded
    common = recommend.next_best_word(db, "ava", limit=15, common_only=True)
    tagged = {r["word"] for r in db.query("MATCH (w:Word {common:true}) RETURN w.text AS word")}
    assert common, "expected curated next-best words"
    assert all(r["word"] in tagged for r in common)


@requires_neo4j
def test_zpd_pool_respects_prerequisites(seeded) -> None:
    """Every ZPD skill is unmastered with ALL prerequisites mastered; locked skills are out."""
    db = seeded
    pool = {r["skill"] for r in recommend.zpd_pool(db, "ava", limit=50)}
    locked = {r["skill"] for r in recommend.locked_skills(db, "ava")}
    assert pool, "expected a non-empty ZPD"
    assert pool.isdisjoint(locked)
    # Ava (taught through 'k') has c+k mastered -> 'ck' is in her ZPD,
    # while 'sh' is locked because 'h' is not yet mastered.
    assert "ck" in pool
    assert "sh" in locked
    for skill in pool:
        unmet = db.query(
            "MATCH (p:Skill)-[:PREREQUISITE_OF]->(s:Skill {key:$k}) "
            "WHERE NOT (:Learner {id:'ava'})-[:MASTERED {mastered:true}]->(p) "
            "RETURN count(p) AS n",
            k=skill,
        )[0]["n"]
        assert unmet == 0


@requires_neo4j
def test_skill_map_statuses_partition_curriculum(seeded) -> None:
    db = seeded
    rows = recommend.skill_map(db, "ava")
    assert rows, "expected curriculum skills"
    assert {r["status"] for r in rows} <= {"mastered", "zpd", "locked"}
    by_status = {s: [r["skill"] for r in rows if r["status"] == s] for s in ("mastered", "zpd")}
    assert "s" in by_status["mastered"]
    assert "ck" in by_status["zpd"]


@requires_neo4j
def test_traverse_zpd_flow(seeded) -> None:
    from rb_neo import traverse

    db = seeded
    s1 = traverse.step_skill_map(db, "ava", "Ava")
    assert s1.dot.startswith("digraph")
    assert s1.extra["counts"]["mastered"] > 0

    s2 = traverse.step_zpd_decision(db, "ava", "Ava")
    assert s2.rows, "expected a non-empty ZPD pool"
    target = s2.extra["target"]
    assert target and s2.extra["pool"][0]["skill"] == target

    s3 = traverse.step_words(db, "ava", "Ava", target)
    assert s3.extra["chips_accepted"], "expected i+1 practice words"
    # Every accepted word's only unmastered grapheme key is the target.
    for w in s3.extra["accepted"]:
        unmastered = db.query(
            "MATCH (l:Learner {id:'ava'}), (x:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme) "
            "WHERE NOT (l)-[:MASTERED {mastered:true}]->(g) "
            "RETURN collect(DISTINCT g.key) AS u",
            wt=w,
        )[0]["u"]
        assert unmastered == [target]

    s4 = traverse.step_ripple(db, "ava", "Ava", target)
    assert s4.extra["unlocked"], "learning the target should unlock words"
    assert s4.extra["after"] >= s4.extra["before"]
    assert s4.dot.startswith("digraph")


@requires_neo4j
def test_traversal_player_html_builds(seeded) -> None:
    from rb_neo.traversal_player import build_traversal_html

    db = seeded
    html = build_traversal_html(db, "ava", "Ava", emoji="🦕")
    assert "vis-network" in html and "Replay traversal" in html
    # The payload must carry the real decision: stages + the chosen target.
    assert '"stages"' in html
    assert "Winner" in html


@requires_neo4j
def test_funnel_counts_narrow_monotonically(seeded) -> None:
    from rb_neo import traverse

    db = seeded
    target = recommend.zpd_pool(db, "ava", limit=1)[0]["skill"]
    stages = traverse.funnel(db, "ava", target)
    counts = [s["count"] for s in stages]
    assert counts == sorted(counts, reverse=True)
    assert counts[-1] > 0


@requires_neo4j
def test_audit_lesson_statuses(seeded) -> None:
    from rb_neo import agent

    db = seeded
    # 'cat' is decodable for Ava; 'kick' is in the safe set being taught;
    # 'the' is a sight word; 'rocket' is off-curriculum.
    audit = agent.audit_lesson(db, "ava", "The cat can kick a rocket.", safe_words=["kick"])
    by_word = {a["word"]: a["status"] for a in audit}
    assert by_word["cat"] == "decodable"
    assert by_word["kick"] == "practice"
    assert by_word["the"] == "sight"
    assert by_word["rocket"] == "flagged"


@requires_neo4j
def test_profile_returns_plan_operators(seeded) -> None:
    from rb_neo.recommend import _ZPD_POOL

    db = seeded
    plan = db.profile(_ZPD_POOL, learner_id="ava", limit=8)
    assert plan, "expected plan operators"
    assert any((r["db_hits"] or 0) > 0 for r in plan)


@requires_neo4j
def test_viz_html_builds(seeded) -> None:
    pytest.importorskip("pyvis")
    from rb_neo import viz

    db = seeded
    words = [r["word"] for r in recommend.cross_word(db, "ben", "sh", limit=4)]
    html = viz.build_word_graph_html(db, words, learner_id="ben", height="200px")
    assert "vis" in html.lower() and len(html) > 1000
