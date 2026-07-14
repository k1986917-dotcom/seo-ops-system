"""One-time/read-only import helper for an existing site's raw export folder."""

from __future__ import annotations

import argparse
from pathlib import Path

from seo_ops.config import get_settings
from seo_ops.db import connection, init_db
from seo_ops.ingest import import_cms_bytes, import_gsc_bytes
from seo_ops.repositories import get_site_by_slug


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default="laserpointerhub")
    parser.add_argument("--raw-root", type=Path, required=True)
    args = parser.parse_args()

    settings = get_settings()
    init_db(settings)
    with connection(settings) as conn:
        site = get_site_by_slug(conn, args.site)
    if not site:
        parser.error(f"unknown site: {args.site}")

    files = [
        *sorted((args.raw_root / "gsc").glob("*.xlsx")),
        *sorted((args.raw_root / "cms").glob("*.json")),
    ]
    if not files:
        parser.error(f"no supported files under: {args.raw_root}")

    failed = False
    for path in files:
        content = path.read_bytes()
        if path.suffix.lower() == ".xlsx":
            outcome = import_gsc_bytes(site["id"], path.name, content, settings)
        else:
            outcome = import_cms_bytes(site["id"], path.name, content, settings)
        print(f"[{outcome.status}] {path.name}: {outcome.message}")
        failed = failed or outcome.status == "failed"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
