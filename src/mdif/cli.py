"""CLI: validate, run, report, dry-run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from mdif.config import load_and_validate
from mdif.engine.graph import topological_layers
from mdif.engine.runner import run_pipeline
from mdif.errors import ConfigError, MDIFError
from mdif.report.generate import load_last_run, write_report


@click.group()
@click.version_option(package_name="mdif")
def main() -> None:
    """Metadata-Driven Ingestion Framework (ADF-style pattern in Python)."""


@main.command("validate")
@click.option("--config", "config_path", default="config/pipeline.yaml", show_default=True)
def validate_cmd(config_path: str) -> None:
    """Validate pipeline YAML against JSON Schema."""
    try:
        cfg = load_and_validate(config_path, resolve_env=False)
        layers = topological_layers(cfg["sources"])
        click.echo(f"OK: {config_path}")
        click.echo(f"Sources: {len(cfg['sources'])}")
        click.echo("Execution layers:")
        for i, layer in enumerate(layers, 1):
            click.echo(f"  {i}. {', '.join(layer)}")
    except ConfigError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)


@main.command("run")
@click.option("--config", "config_path", default="config/pipeline.yaml", show_default=True)
@click.option("--root", default=".", show_default=True, help="Project root working directory")
@click.option("--no-mock-api", is_flag=True, help="Do not start the local mock HTTP API")
def run_cmd(config_path: str, root: str, no_mock_api: bool) -> None:
    """Execute the pipeline."""
    try:
        result = run_pipeline(config_path, root=root, start_mock_api=not no_mock_api)
    except MDIFError as exc:
        click.echo(f"ERROR: {exc}", err=True)
        sys.exit(2)
    click.echo(f"run_id={result.run_id}")
    click.echo(f"status={result.status}")
    for name, src in result.sources.items():
        extra = src.skipped_reason or src.error_message or ""
        click.echo(
            f"  - {name}: {src.status} read={src.rows_read} loaded={src.rows_loaded} "
            f"rejected={src.rows_rejected} attempts={len(src.attempts)} {extra}"
        )
    # Exit non-zero only on unexpected failures
    if result.status == "failed":
        sys.exit(1)


@main.command("dry-run")
@click.option("--config", "config_path", default="config/pipeline.yaml", show_default=True)
@click.option("--root", default=".", show_default=True)
def dry_run_cmd(config_path: str, root: str) -> None:
    """Resolve order and print plan without loading data."""
    result = run_pipeline(config_path, root=root, dry_run=True, start_mock_api=False)
    layers = topological_layers(result.config["sources"])
    click.echo("Dry run plan")
    for i, layer in enumerate(layers, 1):
        click.echo(f"Layer {i}: {', '.join(layer)}")
    click.echo(json.dumps({"lineage": result.lineage}, indent=2))


@main.command("report")
@click.option("--out", "out_dir", default="docs", show_default=True)
@click.option("--state-dir", default="data/state", show_default=True)
def report_cmd(out_dir: str, state_dir: str) -> None:
    """Build static HTML + JSON report from the last run."""
    try:
        data = load_last_run(state_dir)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    path = write_report(data, out_dir)
    click.echo(f"Wrote {path}")
    click.echo(f"Wrote {Path(out_dir) / 'report.json'}")


if __name__ == "__main__":
    main()
