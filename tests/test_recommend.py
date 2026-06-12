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
        # Every returned word's unmastered set is exactly the single 'introduces' grapheme.
        masked = db.query(
            "MATCH (l:Learner {id:'ava'}), (w:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme) "
            "WHERE NOT (l)-[:MASTERED {mastered:true}]->(g) "
            "RETURN collect(DISTINCT g.text) AS unmastered",
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
            "MATCH (w:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme {text:'sh'}) "
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
def test_traverse_decision_and_ripple(seeded) -> None:
    from rb_neo import traverse

    db = seeded
    s2 = traverse.step_decision(db, "ava", "Ava")
    assert s2.rows, "expected candidate words"
    target = s2.extra["target"]
    assert target and s2.extra["chips_accepted"]
    # Every accepted word's only unmastered grapheme is the target.
    for w in s2.extra["accepted"]:
        unmastered = db.query(
            "MATCH (l:Learner {id:'ava'}), (x:Word {text:$wt})-[:HAS_GRAPHEME]->(g:Grapheme) "
            "WHERE NOT (l)-[:MASTERED {mastered:true}]->(g) "
            "RETURN collect(DISTINCT g.text) AS u",
            wt=w,
        )[0]["u"]
        assert unmastered == [target]

    s3 = traverse.step_ripple(db, "ava", "Ava", target)
    assert s3.extra["unlocked"], "learning the target should unlock words"
    assert s3.extra["after"] >= s3.extra["before"]
    assert s3.dot.startswith("digraph")


@requires_neo4j
def test_viz_html_builds(seeded) -> None:
    pytest.importorskip("pyvis")
    from rb_neo import viz

    db = seeded
    words = [r["word"] for r in recommend.cross_word(db, "ben", "sh", limit=4)]
    html = viz.build_word_graph_html(db, words, learner_id="ben", height="200px")
    assert "vis" in html.lower() and len(html) > 1000
