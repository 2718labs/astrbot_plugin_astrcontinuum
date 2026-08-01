"""Command-line entry points for the isolated CRM experiment."""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Sequence
from pathlib import Path

from crm_experiment.runner import materialize_protocol, run_protocol


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crm-experiment")
    commands = parser.add_subparsers(dest="command", required=True)

    materialize = commands.add_parser("materialize")
    materialize.add_argument("--config", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--runtime", type=Path, required=True)
    run.add_argument("--queries", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)

    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--records", type=Path, required=True)
    aggregate.add_argument("--gold", type=Path, required=True)
    aggregate.add_argument("--output", type=Path, required=True)

    render = commands.add_parser("render")
    render.add_argument("--results", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "materialize":
        materialize_protocol(args.config, args.output)
    elif args.command == "run":
        run_protocol(args.config, args.runtime, args.queries, args.output)
    elif args.command == "aggregate":
        reporting = importlib.import_module("crm_experiment.reporting")
        reporting.aggregate_run(args.records, args.gold, args.output)
    elif args.command == "render":
        reporting = importlib.import_module("crm_experiment.reporting")
        reporting.render_report(args.results, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
