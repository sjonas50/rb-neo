"""Typer CLI for rb-neo: init, ingest, reset (more added in later phases)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import typer

from . import agent, browser, recommend
from .config import get_settings
from .curriculum import apply_curriculum
from .db import Neo4jDB, Neo4jUnavailable
from .ingest import ensure_common_words, ensure_words, load_from_dir
from .logging import configure_logging, get_logger
from .synthetic import PROFILES, seed_learners
from .wordlists import ANATOMY_WORDS

app = typer.Typer(help="rb-neo: early-reading knowledge-graph PoC", no_args_is_help=True)
log = get_logger()


@contextmanager
def db_session() -> Iterator[Neo4jDB]:
    """Yield a connected DB, exiting cleanly with a hint if it is unreachable."""
    try:
        with Neo4jDB() as db:
            yield db
    except Neo4jUnavailable as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@app.callback()
def _main() -> None:
    """Configure logging once for all commands."""
    configure_logging()


@app.command()
def init() -> None:
    """Apply schema constraints/indexes and the curriculum skill DAG (idempotent)."""
    with db_session() as db:
        db.apply_schema()
        counts = apply_curriculum(db)
    typer.echo(
        f"Schema applied. Curriculum: {counts['skills']} skills, "
        f"{counts['prerequisites']} prerequisite edges."
    )


@app.command()
def ingest(
    limit: int = typer.Option(8000, help="Max word files to load (0 = all)."),
    sample: int = typer.Option(
        0, help="Randomly sample N files across the whole corpus (0 = off). Use for demos."
    ),
    batch_size: int = typer.Option(500, help="Words per write transaction."),
) -> None:
    """Parse and load word files into the graph."""
    settings = get_settings()
    with db_session() as db:
        summary = load_from_dir(
            db,
            settings.rb_words_dir,
            limit=limit or None,
            sample=sample or None,
            batch_size=batch_size,
        )
        tagged = ensure_common_words(db, settings.rb_words_dir)
        ensure_words(db, settings.rb_words_dir, ANATOMY_WORDS)
    typer.echo(
        f"Ingested {summary['words']} words "
        f"({summary['skipped']} skipped, {summary['minimal_pairs']} minimal pairs, "
        f"{tagged} curated common words tagged)."
    )


@app.command()
def synth() -> None:
    """Create synthetic learners with attempt history + computed mastery."""
    with db_session() as db:
        learners = seed_learners(db)
        for learner in learners:
            s = recommend.mastery_summary(db, learner.id)
            typer.echo(
                f"  {s.get('name')} ({s.get('level')}): "
                f"{s.get('mastered')}/{s.get('skills')} graphemes mastered"
            )
    typer.echo(f"Seeded {len(learners)} learners.")


def _print_rows(rows: list[dict], key: str, empty: str = "(none)") -> None:
    """Echo a compact comma-joined list of one column from query rows."""
    vals = [str(r[key]) for r in rows]
    typer.echo("    " + (", ".join(vals) if vals else empty))


@app.command()
def demo(
    learner: str = typer.Option("", help="Learner id (default: all profiles)."),
) -> None:
    """Run the recommender scenarios for one or all learners."""
    ids = [learner] if learner else [p.learner.id for p in PROFILES]
    with db_session() as db:
        for lid in ids:
            s = recommend.mastery_summary(db, lid)
            if not s:
                typer.echo(f"\nNo learner '{lid}'. Run `rb-neo synth` first.")
                continue
            typer.echo(f"\n{'=' * 60}")
            typer.echo(
                f"{s['name']}  [{s['level']}]  {s['mastered']}/{s['skills']} graphemes mastered"
            )
            typer.echo("=" * 60)

            # The headline recommendation: the ZPD over the curriculum DAG —
            # unmastered skills whose prerequisites are ALL mastered, ranked by
            # how many curated words each would unlock (matches the Streamlit app).
            pool = recommend.zpd_pool(db, lid, limit=6)
            typer.echo("ZPD — next skills (prerequisites met), ranked by words unlocked:")
            for r in pool:
                typer.echo(f"    {r['skill']:<6} +{r['unlocks']:<3} words   ({r['kind']})")
            if pool:
                target = pool[0]["skill"]
                cw = recommend.cross_word(db, lid, target, limit=8)
                typer.echo(f"i+1 practice words for the top skill '{target}':")
                _print_rows(cw, "word")

            nbw = recommend.next_best_word(db, lid, limit=10)
            typer.echo("Raw next-best words (one new grapheme, ignoring prerequisites):")
            for r in nbw:
                typer.echo(
                    f"    {r['word']:<14} + new '{r['introduces']}' ({r['introduces_type']})"
                )

            rem = recommend.remediation(db, lid, limit=5)
            typer.echo("Remediation targets (most-missed, unmastered):")
            _print_rows(rem, "grapheme")

            if rem:
                target = rem[0]["grapheme"]
                cw = recommend.cross_word(db, lid, target, limit=8)
                typer.echo(f"Cross-word practice for '{target}' (rest already known):")
                _print_rows(cw, "word")

            ma = recommend.mastery_aware(db, lid, limit=10)
            typer.echo("Mastery-aware fluency set (fully decodable):")
            _print_rows(ma, "word")

            rq = recommend.review_queue(db, lid, limit=6)
            typer.echo("Review queue (weakest mastered skills):")
            _print_rows(rq, "grapheme")

            # Content-side graph reuse, independent of any learner.
            if ma:
                w = ma[0]["word"]
                typer.echo(f"Rhyme family of '{w}':")
                _print_rows(recommend.rhyme_family(db, w, limit=10), "word")
                typer.echo(f"Minimal pairs of '{w}':")
                _print_rows(recommend.minimal_pairs(db, w, limit=10), "word")
    typer.echo("")


@app.command()
def explain(
    learner: str = typer.Option("ava", help="Learner id to generate guidance for."),
) -> None:
    """Generate a structured teacher recommendation (LLM if configured, else offline)."""
    with db_session() as db:
        rec = agent.explain(db, learner)
    typer.echo(f"Target skill : {rec.target_skill}")
    typer.echo(f"Practice words: {', '.join(rec.words) if rec.words else '(none)'}")
    typer.echo(f"Rationale    : {rec.rationale}")
    if rec.decodable_sentence:
        typer.echo(f"Sentence     : {rec.decodable_sentence}")


@app.command(name="browser-queries")
def browser_queries(
    markdown: bool = typer.Option(
        False, "--markdown", help="Emit docs/neo4j-queries.md content instead of terminal text."
    ),
    key: str = typer.Option("", help="Print only the query with this key (e.g. 'curriculum')."),
) -> None:
    """Print curated Cypher queries for exploring the graph in Neo4j Browser."""
    queries = browser.BROWSER_QUERIES
    if key:
        queries = [q for q in queries if q.key == key]
        if not queries:
            keys = ", ".join(q.key for q in browser.BROWSER_QUERIES)
            typer.secho(f"No query '{key}'. Available: {keys}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
    render = browser.render_markdown if markdown else browser.render_text
    typer.echo(render(queries))


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive delete."),
) -> None:
    """Delete ALL nodes and relationships (destructive)."""
    if not yes:
        typer.confirm("Delete the entire graph?", abort=True)
    with db_session() as db:
        db.reset()
    typer.echo("Graph reset.")


if __name__ == "__main__":
    app()
