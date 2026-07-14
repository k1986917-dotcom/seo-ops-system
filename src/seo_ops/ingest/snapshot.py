from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from seo_ops.config import Settings


@dataclass(frozen=True, slots=True)
class Snapshot:
    sha256: str
    path: Path
    size: int
    reused: bool


def _safe_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    name = re.sub(r"[:\uF03A]Zone\.Identifier$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9._() -]+", "-", name).strip(" .-")
    return name[:160] or "upload.bin"


def save_snapshot(
    settings: Settings,
    site_slug: str,
    source_type: str,
    original_name: str,
    content: bytes,
) -> Snapshot:
    digest = hashlib.sha256(content).hexdigest()
    safe_name = _safe_name(original_name)
    target_dir = settings.snapshots_dir / site_slug / source_type
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = next(target_dir.glob(f"*-{digest[:12]}-*"), None)
    if existing and existing.is_file():
        return Snapshot(sha256=digest, path=existing, size=len(content), reused=True)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{stamp}-{digest[:12]}-{safe_name}"
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_bytes(content)
    temp.replace(target)
    return Snapshot(sha256=digest, path=target, size=len(content), reused=False)
