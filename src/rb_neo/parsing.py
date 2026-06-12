"""Parse ``<word>.json`` files into :class:`WordRecord` graph payloads.

The linguistic logic (grapheme classification, phoneme vowel detection, phonics
pattern extraction, rime keys) lives here so it can be unit-tested and run
offline without a database.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from .models import ChunkUnit, GraphemeUnit, PhonemeUnit, WordRecord

# -- phoneme inventory (ARPABET-style, lowercase as used in sgstRWP) -------------

VOWEL_PHONEMES: frozenset[str] = frozenset(
    {
        "aa", "ae", "ah", "ao", "aw", "ay", "eh", "er",
        "ey", "ih", "iy", "ow", "oy", "uh", "uw",
    }
)

# -- grapheme classification -----------------------------------------------------

CONSONANT_DIGRAPHS: frozenset[str] = frozenset(
    {"ch", "sh", "th", "ph", "wh", "ck", "ng", "gh", "qu", "kn", "wr", "gn", "mb", "ll", "ss"}
)
R_CONTROLLED: frozenset[str] = frozenset(
    {"ar", "er", "ir", "or", "ur", "ear", "eer", "air", "are", "ore", "oar", "our"}
)
VOWEL_TEAMS: frozenset[str] = frozenset(
    {
        "ai", "ay", "ea", "ee", "ei", "ey", "ie", "oa", "oe", "oo",
        "ou", "ow", "oi", "oy", "au", "aw", "ue", "ui", "eu", "igh",
    }
)
# Common morphographic chunks (suffixes / spelling-of-meaning units).
MORPHEMES: frozenset[str] = frozenset(
    {
        "ing", "tion", "sion", "ed", "est", "ly", "ness", "ment",
        "ful", "less", "ous", "age", "able", "ible", "cious", "tious",
    }
)

VOWEL_LETTERS: frozenset[str] = frozenset("aeiou")


def classify_grapheme(text: str) -> str:
    """Classify a grapheme token into a pedagogical type.

    Args:
        text: The raw grapheme as it appears in ``lvlbreaks`` (case preserved).

    Returns:
        One of ``letter``, ``digraph``, ``blend``, ``r_controlled``,
        ``vowel_team`` or ``morpheme``.
    """
    t = text.lower()
    if len(t) == 1:
        return "letter"
    if t in CONSONANT_DIGRAPHS:
        return "digraph"
    if t in R_CONTROLLED:
        return "r_controlled"
    if t in VOWEL_TEAMS:
        return "vowel_team"
    if t in MORPHEMES:
        return "morpheme"
    return "blend"


# -- word text decoding ----------------------------------------------------------


def decode_filename(path: Path) -> tuple[str, str, str]:
    """Decode a word file name into ``(text, written, spoken)``.

    File names are URL-encoded. A ``=`` separates a written symbol from its
    spoken word (e.g. ``100%3Dhundred`` -> written ``100`` / spoken ``hundred``).

    Returns:
        ``(text, written, spoken)`` where ``text`` is the lexical key used as the
        Word node id (the spoken form when a ``=`` is present).
    """
    decoded = unquote(path.stem)
    if "=" in decoded:
        written, spoken = decoded.split("=", 1)
        return spoken or written, written, spoken or written
    return decoded, decoded, decoded


# -- pattern extraction ----------------------------------------------------------


def extract_patterns(graphemes: list[GraphemeUnit], phonemes: list[PhonemeUnit]) -> list[str]:
    """Derive coarse phonics patterns exemplified by a word.

    These become :class:`Pattern` nodes so words that teach the same skill are
    discoverable in one hop, regardless of which specific letters they use.
    """
    patterns: set[str] = set()
    types = {g.type for g in graphemes}
    if "digraph" in types:
        patterns.add("has_digraph")
    if "blend" in types:
        patterns.add("has_blend")
    if "vowel_team" in types:
        patterns.add("has_vowel_team")
    if "r_controlled" in types:
        patterns.add("has_r_controlled")
    if "morpheme" in types:
        patterns.add("has_morpheme")

    # CVC: three single-letter graphemes, consonant-vowel-consonant.
    if len(graphemes) == 3 and all(g.type == "letter" for g in graphemes):
        c1, v, c2 = (g.text.lower() for g in graphemes)
        if c1 not in VOWEL_LETTERS and v in VOWEL_LETTERS and c2 not in VOWEL_LETTERS:
            patterns.add("CVC")

    # silent-e: ends in a single 'e' whose final phoneme is not a vowel sound.
    if graphemes and graphemes[-1].text.lower() == "e" and graphemes[-1].type == "letter":
        if phonemes and not phonemes[-1].is_vowel:
            patterns.add("silent_e")

    return sorted(patterns)


def rime_key(phonemes: list[PhonemeUnit]) -> str:
    """Build a rhyme key: phonemes from the last vowel sound to the word end.

    Words that share this key rhyme (e.g. ``cat``/``hat`` -> ``ae+t``). Returns
    an empty string when the word has no vowel phoneme.
    """
    last_vowel = -1
    for i, p in enumerate(phonemes):
        if p.is_vowel:
            last_vowel = i
    if last_vowel == -1:
        return ""
    return "+".join(p.text for p in phonemes[last_vowel:])


# -- main parser -----------------------------------------------------------------


def _pair_sounds(breaks: list[str], sounds: list[str]) -> list[tuple[str, str]]:
    """Zip grapheme/chunk breaks with their parallel sound list, padding sounds."""
    out: list[tuple[str, str]] = []
    for i, b in enumerate(breaks):
        snd = sounds[i] if i < len(sounds) else ""
        out.append((b, snd))
    return out


def parse_word_file(path: Path) -> WordRecord | None:
    """Parse a single word JSON file into a :class:`WordRecord`.

    Returns ``None`` if the file is unreadable or has no word payload.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        w = data["words"][0]
    except (json.JSONDecodeError, KeyError, IndexError, OSError):
        return None

    text, written, spoken = decode_filename(path)

    graphemes: list[GraphemeUnit] = []
    for i, (g, snd) in enumerate(_pair_sounds(w.get("lvlbreaks", []), w.get("audsounds", []))):
        if g == "":
            continue
        graphemes.append(
            GraphemeUnit(pos=i, text=g, type=classify_grapheme(g), length=len(g), sound=snd)
        )

    chunks: list[ChunkUnit] = []
    for i, (c, snd) in enumerate(_pair_sounds(w.get("lvlbreaksB", []), w.get("audsoundsB", []))):
        if c:
            chunks.append(ChunkUnit(pos=i, text=c, level="B", sound=snd))
    for i, (c, snd) in enumerate(_pair_sounds(w.get("lvlbreaksC", []), w.get("audsoundsC", []))):
        if c:
            chunks.append(ChunkUnit(pos=i, text=c, level="C", sound=snd))

    phonemes: list[PhonemeUnit] = []
    for i, p in enumerate(t for t in w.get("sgstRWP", "").split("+") if t):
        phonemes.append(PhonemeUnit(pos=i, text=p, is_vowel=p in VOWEL_PHONEMES))

    return WordRecord(
        text=text,
        written=written,
        spoken=spoken,
        prlevel=w.get("prlevel", ""),
        rwp=w.get("sgstRWP", ""),
        syllables_raw=w.get("Syllables", ""),
        audio=w.get("wordaud", ""),
        graphemes=graphemes,
        chunks=chunks,
        phonemes=phonemes,
        patterns=extract_patterns(graphemes, phonemes),
        rime_key=rime_key(phonemes),
    )


def iter_word_files(words_dir: str | Path, limit: int | None = None) -> list[Path]:
    """Return sorted word file paths under ``words_dir`` (optionally capped)."""
    paths = sorted(Path(words_dir).glob("*.json"))
    return paths[:limit] if limit else paths
