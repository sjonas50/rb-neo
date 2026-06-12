"""Typer CLI for rb-neo: init, ingest, reset (more added in later phases)."""

from __future__ import annotations

import typer

from . import agent, recommend
from .config import get_settings
from .db import Neo4jDB
from .ingest import load_from_dir
from .logging import configure_logging, get_logger
from .synthetic import PROFILES, seed_learners

app = typer.Typer(help="rb-neo: early-reading knowledge-graph PoC", no_args_is_help=True)
log = get_logger()


@app.callback()
def _main() -> None:
    """Configure logging once for all commands."""
    configure_logging()


@app.command()
def init() -> None:
    """Apply schema constraints and indexes (idempotent)."""
    with Neo4jDB() as db:
        db.apply_schema()
    typer.echo("Schema applied.")


@app.command()
def ingest(
    limit: int = typer.Option(8000, help="Max word files to load (0 = all)."),
    batch_size: int = typer.Option(500, help="Words per write transaction."),
) -> None:
    """Parse and load word files into the graph."""
    settings = get_settings()
    with Neo4jDB(settings) as db:
        summary = load_from_dir(
            db, settings.rb_words_dir, limit=limit or None, batch_size=batch_size
        )
    typer.echo(
        f"Ingested {summary['words']} words "
        f"({summary['skipped']} skipped, {summary['minimal_pairs']} minimal pairs)."
    )


@app.command()
def synth() -> None:
    """Create synthetic learners with attempt history + computed mastery."""
    with Neo4jDB() as db:
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
    with Neo4jDB() as db:
        for lid in ids:
            s = recommend.mastery_summary(db, lid)
            if not s:
                typer.echo(f"\nNo learner '{lid}'. Run `rb-neo synth` first.")
                continue
            typer.echo(f"\n{'=' * 60}")
            typer.echo(
                f"{s['name']}  [{s['level']}]  "
                f"{s['mastered']}/{s['skills']} graphemes mastered"
            )
            typer.echo("=" * 60)

            nbw = recommend.next_best_word(db, lid, limit=10)
            typer.echo("Next-best words (introduce exactly ONE new grapheme):")
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
    with Neo4jDB() as db:
        rec = agent.explain(db, learner)
    typer.echo(f"Target skill : {rec.target_skill}")
    typer.echo(f"Practice words: {', '.join(rec.words) if rec.words else '(none)'}")
    typer.echo(f"Rationale    : {rec.rationale}")
    if rec.decodable_sentence:
        typer.echo(f"Sentence     : {rec.decodable_sentence}")


@app.command()
def reset(
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm destructive delete."),
) -> None:
    """Delete ALL nodes and relationships (destructive)."""
    if not yes:
        typer.confirm("Delete the entire graph?", abort=True)
    with Neo4jDB() as db:
        db.reset()
    typer.echo("Graph reset.")


if __name__ == "__main__":
    app()
