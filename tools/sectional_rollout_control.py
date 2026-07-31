#!/usr/bin/env python3
"""Inspect sectional rollout policy or execute an explicit manifest rollback."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from seo_ops.config import get_settings
from seo_ops.services.sectional_rollout import (
    build_rollout_policy,
    rollback_sectional_promotion,
    rollout_decision,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    decision = sub.add_parser("decision", help="show rollout decision for one Action")
    decision.add_argument("--action-id", type=int, required=True)

    rollback = sub.add_parser("rollback", help="restore the exact Legacy pair from a manifest")
    rollback.add_argument("--manifest", type=Path, required=True)
    rollback.add_argument(
        "--confirm",
        required=True,
        help="must be exactly ROLLBACK",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "decision":
        settings = get_settings()
        policy = build_rollout_policy(
            settings.sectional_writing_mode,
            list(settings.sectional_action_allowlist),
        )
        payload = rollout_decision(policy, args.action_id)
    else:
        if args.confirm != "ROLLBACK":
            raise SystemExit("rollback confirmation must be exactly ROLLBACK")
        payload = rollback_sectional_promotion(args.manifest)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
