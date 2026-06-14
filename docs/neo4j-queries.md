# Exploring rb-neo in Neo4j Browser

> **Generated** from `rb_neo.browser` — do not edit by hand. Regenerate with `uv run rb-neo browser-queries --markdown > docs/neo4j-queries.md`.

Neo4j Browser ships with the database. With the stack running (`docker compose up -d`), open <http://localhost:7474> and log in with the `NEO4J_USER` / `NEO4J_PASSWORD` from your `.env` (defaults: `neo4j` / `rbneopass`).

Paste any query below into the bar at the top. Queries that `RETURN` whole nodes/paths render as an interactive graph; queries that return scalars render as a table. Use the style panel (click a node, or the gear at bottom-left) to set captions and colors.

## Contents

1. [Scale & reuse — why a graph at all](#scale--reuse-why-a-graph-at-all)
2. [The curriculum prerequisite DAG](#the-curriculum-prerequisite-dag)
3. [What a composite skill depends on](#what-a-composite-skill-depends-on)
4. [A learner's mastery over the curriculum](#a-learners-mastery-over-the-curriculum)
5. [The ZPD frontier — mastered vs. the next reachable skill](#the-zpd-frontier-mastered-vs-the-next-reachable-skill)
6. [Shared sub-word units — the reuse made literal](#shared-sub-word-units-the-reuse-made-literal)
7. [One word, fully decomposed](#one-word-fully-decomposed)
8. [The ripple — one new skill unlocks a wave of words](#the-ripple-one-new-skill-unlocks-a-wave-of-words)
9. [Rhyme families — shared Rime nodes](#rhyme-families-shared-rime-nodes)
10. [Minimal pairs — one phoneme apart](#minimal-pairs-one-phoneme-apart)
11. [The busiest shared nodes](#the-busiest-shared-nodes)

## Scale & reuse — why a graph at all

Tens of thousands of words collapse onto a tiny shared vocabulary of sub-word units. That reuse is the whole argument for a graph.

```cypher
MATCH (w:Word)   WITH count(w)  AS words
MATCH (g:Grapheme) WITH words, count(g) AS graphemes
MATCH (s:Sound)  WITH words, graphemes, count(s)  AS sounds
MATCH (p:Phoneme) WITH words, graphemes, sounds, count(p) AS phonemes
MATCH (sk:Skill)
RETURN words, graphemes, sounds, phonemes, count(sk) AS skills
```

> 💡 Returns a table (scalars). The next queries return graphs.

## The curriculum prerequisite DAG

The phonics scope & sequence as a graph: 42 Skill nodes, 28 PREREQUISITE_OF edges. A composite skill requires its component letters — 'sh' needs 's' and 'h'.

```cypher
MATCH p=(:Skill)-[:PREREQUISITE_OF]->(:Skill)
RETURN p
```

> 💡 Click a node → in the right panel set the caption to `key`.

## What a composite skill depends on

The digraphs/teams that gate a learner's progress, with their prerequisites.

```cypher
MATCH (pre:Skill)-[:PREREQUISITE_OF]->(s:Skill)
WHERE s.key IN ['sh','ch','th','ck','ai','oa','ar','ee']
RETURN pre, s
```

## A learner's mastery over the curriculum

The learner overlay: which Skill nodes Ava has mastered. Swap 'ava' for 'ben', 'maya', or 'cara'.

```cypher
MATCH (l:Learner {id: 'ava'})-[m:MASTERED {mastered: true}]->(s:Skill)
RETURN l, m, s
```

> 💡 Color the MASTERED edge or Skill nodes via the right-hand style panel.

## The ZPD frontier — mastered vs. the next reachable skill

Ava's mastered skills plus the prerequisite edges pointing at what they unlock next. The gap between green and grey is the zone of proximal development.

```cypher
MATCH (l:Learner {id: 'ava'})
MATCH (pre:Skill)-[e:PREREQUISITE_OF]->(s:Skill)
WHERE (l)-[:MASTERED {mastered: true}]->(pre)
RETURN pre, e, s
```

## Shared sub-word units — the reuse made literal

A handful of words decomposed into graphemes. The shared nodes (ck, i, c, k…) visibly connect multiple words — singletons by MERGE-on-key.

```cypher
MATCH (w:Word)-[:HAS_GRAPHEME]->(g:Grapheme)
WHERE w.text IN ['kick','sock','pick','sick','tick','tack','pack']
RETURN w, g
```

## One word, fully decomposed

Every layer the corpus carries for a single word: graphemes, the sounds they produce, and ARPABET phonemes.

```cypher
MATCH (w:Word {text: 'ship'})
OPTIONAL MATCH (w)-[:HAS_GRAPHEME]->(g:Grapheme)
OPTIONAL MATCH (g)-[:PRODUCES_SOUND]->(s:Sound)
OPTIONAL MATCH (w)-[:HAS_PHONEME]->(p:Phoneme)
RETURN w, g, s, p
```

> 💡 Swap 'ship' for any word in the corpus.

## The ripple — one new skill unlocks a wave of words

Words that become fully decodable for Ava the moment she learns 'ck': every other grapheme is already mastered. This is step 4 of the app.

```cypher
MATCH (l:Learner {id: 'ava'}), (t:Grapheme {key: 'ck'})
MATCH (w:Word)-[:HAS_GRAPHEME]->(t)
WHERE w.common = true AND w.text =~ '[a-z]{3,}'
  AND NOT EXISTS {
    MATCH (w)-[:HAS_GRAPHEME]->(o:Grapheme)
    WHERE o.key <> 'ck' AND NOT (l)-[:MASTERED {mastered: true}]->(o)
  }
RETURN t, w
```

## Rhyme families — shared Rime nodes

Rhyme is modeled as a shared Rime node, not pairwise edges. Words on the same Rime rhyme by sound (kick/sick/tick), not spelling.

```cypher
MATCH (w:Word {text: 'kick'})-[:HAS_RIME]->(r:Rime)<-[:HAS_RIME]-(o:Word)
WHERE o.common = true
RETURN w, r, o
```

## Minimal pairs — one phoneme apart

Words differing by exactly one phoneme — discrimination practice, derived at ingest via phoneme-wildcard hashing.

```cypher
MATCH (w:Word {text: 'cat'})-[m:MINIMAL_PAIR_OF]-(o:Word)
WHERE o.common = true
RETURN w, m, o
```

## The busiest shared nodes

The graphemes reused across the most words — the hubs that make the graph small relative to the corpus.

```cypher
MATCH (g:Grapheme)<-[:HAS_GRAPHEME]-(w:Word)
WITH g, count(w) AS uses
RETURN g.text AS grapheme, g.type AS type, uses
ORDER BY uses DESC LIMIT 15
```

> 💡 Returns a table — the reuse counts behind the 'scale' query.

