"""Tests for parsing + ingestion. Unit tests run offline; integration needs Neo4j."""

from __future__ import annotations

from pathlib import Path

import pytest

from rb_neo.ingest import derive_minimal_pairs, load_from_dir
from rb_neo.models import GraphemeUnit, PhonemeUnit, WordRecord
from rb_neo.parsing import (
    align_containment,
    align_gpc,
    classify_grapheme,
    decode_filename,
    parse_word_file,
)

from .conftest import requires_neo4j

WORDS_DIR = Path(__file__).resolve().parents[1] / "words"


# -- unit: classification --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("c", "letter"),
        ("sh", "digraph"),
        ("ch", "digraph"),
        ("ar", "r_controlled"),
        ("ea", "vowel_team"),
        ("tion", "morpheme"),
        ("str", "blend"),
    ],
)
def test_classify_grapheme(text: str, expected: str) -> None:
    assert classify_grapheme(text) == expected


def test_decode_filename_symbol() -> None:
    text, written, spoken = decode_filename(Path("100%3Dhundred.json"))
    assert (text, written, spoken) == ("hundred", "100", "hundred")


def test_decode_filename_plain() -> None:
    assert decode_filename(Path("cat.json")) == ("cat", "cat", "cat")


# -- unit: real-file parsing -----------------------------------------------------


@pytest.mark.skipif(not (WORDS_DIR / "cat.json").exists(), reason="words corpus not present")
def test_parse_cat() -> None:
    rec = parse_word_file(WORDS_DIR / "cat.json")
    assert rec is not None
    assert rec.text == "cat"
    assert [g.text for g in rec.graphemes] == ["c", "a", "t"]
    assert rec.phoneme_seq == ("k", "ae", "t")
    assert rec.rime_key == "ae+t"
    assert "CVC" in rec.patterns


# -- unit: cross-level structure -------------------------------------------------


def test_align_containment_nests_by_offset() -> None:
    # rocket: syllables [rock, et] contain chunks [r, ock, et].
    pairs = align_containment(["rock", "et"], ["r", "ock", "et"])
    assert ("rock", "r") in pairs
    assert ("rock", "ock") in pairs
    assert ("et", "et") in pairs
    # No chunk is assigned to two syllables.
    assert len(pairs) == 3


def test_align_gpc_one_to_one_only() -> None:
    g = [GraphemeUnit(pos=i, text=t, type="letter", length=len(t), sound=f"s{i}")
         for i, t in enumerate(["c", "a", "t"])]
    p = [PhonemeUnit(pos=i, text=t, is_vowel=t == "ae") for i, t in enumerate(["k", "ae", "t"])]
    gpc, sound_phoneme = align_gpc(g, p)
    assert gpc == [("c", "k"), ("a", "ae"), ("t", "t")]
    assert sound_phoneme == [("s0", "k"), ("s1", "ae"), ("s2", "t")]
    # Count mismatch (e.g. silent-e: 4 graphemes, 3 phonemes) -> nothing emitted.
    assert align_gpc(g, p[:2]) == ([], [])


@pytest.mark.skipif(not (WORDS_DIR / "rocket.json").exists(), reason="words corpus not present")
def test_parse_rocket_hierarchy() -> None:
    rec = parse_word_file(WORDS_DIR / "rocket.json")
    assert rec is not None
    assert ("rock", "ock") in rec.contains_chunk
    assert ("ock", "ck") in rec.contains_grapheme
    assert ("ck", "k") in rec.gpc


# -- unit: minimal pairs ---------------------------------------------------------


def _word(text: str, phonemes: list[str]) -> WordRecord:
    return WordRecord(
        text=text,
        written=text,
        spoken=text,
        phonemes=[PhonemeUnit(pos=i, text=p, is_vowel=False) for i, p in enumerate(phonemes)],
    )


def test_minimal_pairs_detects_single_diff() -> None:
    records = [
        _word("cat", ["k", "ae", "t"]),
        _word("cot", ["k", "aa", "t"]),  # differs from cat by 1 phoneme
        _word("dog", ["d", "aa", "g"]),  # differs from cot by 2 -> not a pair
    ]
    pairs = derive_minimal_pairs(records)
    got = {tuple(sorted((p["a"], p["b"]))) for p in pairs}
    assert ("cat", "cot") in got
    assert ("cot", "dog") not in got


# -- integration: ingestion + shared-node reuse ----------------------------------


@requires_neo4j
def test_ingest_shared_nodes(db) -> None:
    summary = load_from_dir(db, WORDS_DIR, limit=300)
    assert summary["words"] > 0

    # Words exist.
    n_words = db.query("MATCH (w:Word) RETURN count(w) AS n")[0]["n"]
    assert n_words == summary["words"]

    # Shared-node reuse: a single grapheme is reused across many words.
    rows = db.query(
        "MATCH (:Word)-[:HAS_GRAPHEME]->(g:Grapheme) "
        "RETURN g.text AS t, count(*) AS uses ORDER BY uses DESC LIMIT 1"
    )
    assert rows[0]["uses"] > 1

    # Sounds and phonemes are far fewer than words (the graph's value prop).
    n_sounds = db.query("MATCH (s:Sound) RETURN count(s) AS n")[0]["n"]
    assert 0 < n_sounds < summary["words"]
