"""Run one controlled existing-pair sectional shadow through the formal Web route."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from seo_ops.__main__ import _load_local_env
from seo_ops.config import get_settings


class ShadowRunnerError(RuntimeError):
    """Raised when the controlled runner cannot safely complete."""


def _slugify(text: str) -> str:
    """Match the Legacy canonical slug rule without importing repo-root modules."""
    value = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    value = re.sub(r"-+", "-", value)
    if value:
        return value
    digest = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:12]
    return f"topic-{digest}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Start an isolated local SEO Ops server, send exactly one existing-pair "
            "sectional shadow POST, and verify the formal Action state is unchanged."
        )
    )
    parser.add_argument("--action-id", type=int, required=True)
    parser.add_argument("--author", default="LaserPointerHub")
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--ai-call-limit", type=int, default=24)
    parser.add_argument("--startup-timeout", type=int, default=60)
    parser.add_argument("--request-timeout", type=int, default=3600)
    parser.add_argument(
        "--resume-existing",
        action="store_true",
        help=(
            "Resume only from known sectional checkpoint/intermediate files. "
            "Complete, promoted, or unknown artifact sets are refused."
        ),
    )
    parser.add_argument(
        "--refresh-complete",
        action="store_true",
        help=(
            "With --resume-existing, archive a reviewed complete shadow candidate "
            "outside the active sectional root before starting a new contract run. "
            "Promotion manifests, incomplete terminal sets, and unknown files are refused."
        ),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Defaults to /tmp/seo-sectional-shadow-action-<id>.log.",
    )
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        args,
        cwd=repo,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def _git_state(repo: Path) -> dict[str, Any]:
    branch = _command(repo, "git", "branch", "--show-current")
    head = _command(repo, "git", "rev-parse", "HEAD")
    status = _command(repo, "git", "status", "--short")
    try:
        counts = _command(
            repo,
            "git",
            "rev-list",
            "--left-right",
            "--count",
            "HEAD...@{upstream}",
        ).split()
        ahead, behind = (int(counts[0]), int(counts[1]))
    except (subprocess.CalledProcessError, ValueError, IndexError):
        ahead = behind = -1
    return {
        "branch": branch,
        "head": head,
        "clean": not status,
        "ahead": ahead,
        "behind": behind,
    }


def _read_action(database: Path, action_id: int) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT id, site_id, legacy_stage, workflow_status, target_ref
            FROM actions
            WHERE id = ?
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            raise ShadowRunnerError(f"Action #{action_id} does not exist")
        return dict(row)
    finally:
        connection.close()


def _ai_run_count(database: Path) -> int:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM ai_runs").fetchone()[0])
    finally:
        connection.close()


def _paths(data_dir: Path, action_id: int, topic: str) -> dict[str, Path]:
    slug = _slugify(topic)
    workspace = (
        data_dir
        / "legacy_workflow"
        / "laserpointerhub"
        / "runs"
        / f"action-{action_id}"
        / "current"
        / "laserpointerhub"
    )
    draft_candidates = [
        path for path in (workspace / "drafts").glob(f"{slug}-*.md") if path.is_file()
    ]
    if not draft_candidates:
        raise ShadowRunnerError("formal Legacy draft is missing")
    draft = max(draft_candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
    return {
        "workspace": workspace,
        "draft": draft,
        "claim": workspace / "research" / f"claim-ledger-{slug}.json",
        "state": workspace / "reports" / f"w2-state-{slug}.json",
        "sectional_root": workspace / "drafts" / "sectional" / slug,
    }


def _formal_hashes(paths: dict[str, Path], env_file: Path) -> dict[str, str]:
    required = {
        "draft": paths["draft"],
        "claim": paths["claim"],
        "state": paths["state"],
        "env": env_file,
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise ShadowRunnerError(f"required formal files are missing: {missing}")
    return {name: _sha256(path) for name, path in required.items()}


def _validate_resumable_root(root: Path, action_id: int) -> list[str]:
    files = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
    if not files:
        raise ShadowRunnerError("sectional root exists but contains no resumable files")
    forbidden = {
        "assembled-draft.md",
        "assembled-claim-ledger.json",
        "assembly-report.json",
        f"shadow-comparison-action-{action_id}.json",
        "promotion-manifest.json",
    }
    if forbidden.intersection(files):
        raise ShadowRunnerError(
            "sectional root contains complete or promotion artifacts; refusing resume"
        )
    checkpoint = re.compile(r"checkpoints/(?:section-[a-f0-9]{10}|article-frame)\.json")
    ledger_checkpoint = re.compile(
        r"ledger-checkpoints/(?:section-[a-f0-9]{10}|"
        r"frame-(?:introduction|takeaways|conclusion|faq))\.json"
    )
    unknown = [
        item
        for item in files
        if item != "resolved-delivery.json"
        and not checkpoint.fullmatch(item)
        and not ledger_checkpoint.fullmatch(item)
    ]
    if unknown:
        raise ShadowRunnerError(f"sectional root contains unknown resume artifacts: {unknown}")
    return files


def _complete_artifact_names(action_id: int) -> set[str]:
    return {
        "assembled-draft.md",
        "assembled-claim-ledger.json",
        "assembly-report.json",
        f"shadow-comparison-action-{action_id}.json",
    }


def _archive_complete_candidate(
    root: Path,
    action_id: int,
    git_head: str,
) -> dict[str, Any]:
    """Move one reviewed terminal candidate into a preserved sibling archive.

    Checkpoints remain in the active root so the formal resume logic can decide
    which packages are still reusable.  The old resolved delivery is archived
    with the four terminal artifacts to prevent stale delivery data from being
    mistaken for the next candidate if the new run stops before Phase 4.
    """
    files = sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
    file_set = set(files)
    promotion = "promotion-manifest.json"
    if promotion in file_set:
        raise ShadowRunnerError("sectional root contains a promotion manifest; refusing refresh")

    complete = _complete_artifact_names(action_id)
    present_complete = complete.intersection(file_set)
    if not present_complete:
        raise ShadowRunnerError("--refresh-complete requires one complete shadow candidate")
    if present_complete != complete:
        missing = sorted(complete - present_complete)
        raise ShadowRunnerError(
            f"sectional root contains an incomplete terminal candidate: missing {missing}"
        )

    checkpoint = re.compile(r"checkpoints/(?:section-[a-f0-9]{10}|article-frame)\.json")
    ledger_checkpoint = re.compile(
        r"ledger-checkpoints/(?:section-[a-f0-9]{10}|"
        r"frame-(?:introduction|takeaways|conclusion|faq))\.json"
    )
    allowed_terminal = complete | {"resolved-delivery.json"}
    unknown = [
        item
        for item in files
        if item not in allowed_terminal
        and not checkpoint.fullmatch(item)
        and not ledger_checkpoint.fullmatch(item)
    ]
    if unknown:
        raise ShadowRunnerError(f"sectional root contains unknown refresh artifacts: {unknown}")

    archive_names = sorted(item for item in allowed_terminal if item in file_set)
    comparison = root / f"shadow-comparison-action-{action_id}.json"
    comparison_sha = _sha256(comparison)[:12]
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    nonce = f"{time.time_ns() % 1_000_000_000:09d}"
    archive_id = f"{stamp}-{nonce}-{comparison_sha}"
    archive_parent = root.parent.parent / "sectional-archive" / root.name
    archive_parent.mkdir(parents=True, exist_ok=True)
    temp_archive = archive_parent / f".tmp-{archive_id}"
    final_archive = archive_parent / archive_id
    temp_archive.mkdir()

    hashes = {name: _sha256(root / name) for name in archive_names}
    moved: list[str] = []
    try:
        for name in archive_names:
            source = root / name
            destination = temp_archive / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            moved.append(name)

        for name, expected_sha in hashes.items():
            if _sha256(temp_archive / name) != expected_sha:
                raise ShadowRunnerError(f"archived artifact hash mismatch before commit: {name}")

        manifest = {
            "version": 1,
            "action_id": action_id,
            "archived_at_utc": f"{stamp[:-1]}.{nonce}Z",
            "source_root": str(root),
            "git_head": git_head,
            "artifacts": hashes,
        }
        manifest_path = temp_archive / "archive-manifest.json"
        with manifest_path.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_archive, final_archive)
    except Exception as exc:
        rollback_root = final_archive if final_archive.exists() else temp_archive
        rollback_errors: list[str] = []
        for name in reversed(moved):
            archived = rollback_root / name
            if archived.exists():
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    os.replace(archived, destination)
                except Exception as rollback_exc:
                    rollback_errors.append(f"{name}: {rollback_exc}")
        if rollback_errors:
            raise ShadowRunnerError(
                "failed to archive complete shadow candidate and rollback was incomplete; "
                f"recovery files preserved at {rollback_root}: {rollback_errors}"
            ) from exc
        shutil.rmtree(rollback_root, ignore_errors=True)
        raise ShadowRunnerError(
            f"failed to archive complete shadow candidate; active files restored: {exc}"
        ) from exc

    return {
        "archive_id": archive_id,
        "archive_path": str(final_archive),
        "git_head": git_head,
        "artifacts": hashes,
        "manifest_sha256": _sha256(final_archive / "archive-manifest.json"),
    }


def _port_is_free(host: str, port: int) -> bool:
    probe = socket.socket()
    probe.settimeout(0.5)
    try:
        return probe.connect_ex((host, port)) != 0
    finally:
        probe.close()


def _child_env(port: int, ai_call_limit: int) -> dict[str, str]:
    child = os.environ.copy()
    child.update(
        {
            "SEO_OPS_SECTIONAL_WRITING_MODE": "shadow",
            "SEO_OPS_SECTIONAL_ACTION_ALLOWLIST": "",
            "SEO_OPS_SECTIONAL_AI_CALL_LIMIT": str(ai_call_limit),
            "SEO_OPS_HOST": "127.0.0.1",
            "SEO_OPS_PORT": str(port),
        }
    )
    return child


def _wait_for_health(port: int, process: subprocess.Popen[str], timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise ShadowRunnerError(f"server exited before health check: {process.returncode}")
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health",
                timeout=2,
            ) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except Exception:
            time.sleep(1)
    raise ShadowRunnerError("server did not become healthy")


def _health_is_ready(health: dict[str, Any]) -> bool:
    return health.get("status") == "ok" and health.get("ai_enabled") is True


def _post_once(action_id: int, author: str, port: int, timeout: int) -> dict[str, Any]:
    body = urllib.parse.urlencode({"author": author}).encode("utf-8")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(
            "POST",
            f"/actions/{action_id}/legacy/stage/sectional-shadow",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        status = response.status
        headers = dict(response.getheaders())
        response.read()
    finally:
        connection.close()
    location = headers.get("Location") or headers.get("location", "")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    return {
        "status": status,
        "location": location,
        "level": (query.get("level") or [""])[0],
        "message": (query.get("message") or [""])[0],
        "post_attempts": 1,
    }


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
    except ProcessLookupError:
        pass


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if args.action_id <= 0:
        raise ShadowRunnerError("action-id must be positive")
    if not 8 <= args.ai_call_limit <= 40:
        raise ShadowRunnerError("ai-call-limit must be between 8 and 40")

    _load_local_env()
    get_settings.cache_clear()
    settings = get_settings()
    repo = settings.project_root
    env_file = repo / ".env"
    git_before = _git_state(repo)
    if not git_before["clean"]:
        raise ShadowRunnerError("git working tree must be clean")
    if git_before["ahead"] != 0 or git_before["behind"] != 0:
        raise ShadowRunnerError("branch must be synchronized with its upstream")
    if settings.sectional_writing_mode != "off":
        raise ShadowRunnerError("default sectional rollout must be off before the run")
    if settings.sectional_action_allowlist:
        raise ShadowRunnerError("default sectional action allowlist must be empty")

    action_before = _read_action(settings.database_path, args.action_id)
    paths = _paths(settings.data_dir, args.action_id, action_before["target_ref"])
    root = paths["sectional_root"]
    partial_artifacts_before: list[str] = []
    archived_complete_candidate: dict[str, Any] | None = None
    refresh_complete = bool(getattr(args, "refresh_complete", False))
    if refresh_complete and not args.resume_existing:
        raise ShadowRunnerError("--refresh-complete requires --resume-existing")
    if root.exists():
        if not args.resume_existing:
            raise ShadowRunnerError(
                "sectional root already exists; use --resume-existing only after review"
            )
        if not refresh_complete:
            partial_artifacts_before = _validate_resumable_root(root, args.action_id)
    elif args.resume_existing:
        raise ShadowRunnerError("--resume-existing requires an existing sectional root")
    if not _port_is_free("127.0.0.1", args.port):
        raise ShadowRunnerError(f"port {args.port} is already in use")

    formal_before = _formal_hashes(paths, env_file)
    db_sha_before = _sha256(settings.database_path)
    ai_runs_before = _ai_run_count(settings.database_path)
    log_file = args.log_file or Path(f"/tmp/seo-sectional-shadow-action-{args.action_id}.log")
    log_file.unlink(missing_ok=True)
    process: subprocess.Popen[str] | None = None
    http_result: dict[str, Any] = {"post_attempts": 0}
    error = ""
    health: dict[str, Any] = {}
    with log_file.open("w", encoding="utf-8") as log_handle:
        try:
            if refresh_complete:
                archived_complete_candidate = _archive_complete_candidate(
                    root,
                    args.action_id,
                    git_before["head"],
                )
                partial_artifacts_before = _validate_resumable_root(root, args.action_id)
            process = subprocess.Popen(
                [sys.executable, "-m", "seo_ops"],
                cwd=repo,
                env=_child_env(args.port, args.ai_call_limit),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            health = _wait_for_health(args.port, process, args.startup_timeout)
            if not _health_is_ready(health):
                raise ShadowRunnerError(
                    "server health check must report status=ok and ai_enabled=true"
                )
            http_result = _post_once(
                args.action_id,
                args.author,
                args.port,
                args.request_timeout,
            )
        except Exception as exc:
            error = str(exc)
        finally:
            _stop(process)

    formal_after = _formal_hashes(paths, env_file)
    action_after = _read_action(settings.database_path, args.action_id)
    ai_runs_after = _ai_run_count(settings.database_path)
    db_sha_after = _sha256(settings.database_path)
    git_after = _git_state(repo)
    artifacts = (
        sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())
        if root.exists()
        else []
    )
    promotion_manifest = root / "promotion-manifest.json"
    formal_unchanged = formal_before == formal_after
    action_unchanged = action_before == action_after
    safe = (
        formal_unchanged
        and action_unchanged
        and not promotion_manifest.exists()
        and git_after == git_before
    )
    route_success = (
        http_result.get("status") == 303
        and http_result.get("level") != "error"
        and "章节化 shadow 已完成" in http_result.get("message", "")
    )
    required_artifacts = {
        "assembled-draft.md",
        "assembled-claim-ledger.json",
        "assembly-report.json",
        f"shadow-comparison-action-{args.action_id}.json",
    }
    artifact_names = {Path(item).name for item in artifacts}
    artifacts_complete = required_artifacts <= artifact_names
    report = {
        "result": "success" if route_success and safe and artifacts_complete else "failed",
        "action_id": args.action_id,
        "git": git_after,
        "health": health,
        "http": http_result,
        "error": error,
        "formal_hashes_before": formal_before,
        "formal_hashes_after": formal_after,
        "formal_unchanged": formal_unchanged,
        "action_before": action_before,
        "action_after": action_after,
        "action_unchanged": action_unchanged,
        "database_sha256": {
            "before": db_sha_before,
            "after": db_sha_after,
            "changed": db_sha_before != db_sha_after,
        },
        "ai_runs": {
            "before": ai_runs_before,
            "after": ai_runs_after,
            "added": ai_runs_after - ai_runs_before,
        },
        "sectional_root": str(root),
        "resume_existing": args.resume_existing,
        "refresh_complete": refresh_complete,
        "archived_complete_candidate": archived_complete_candidate,
        "partial_artifacts_before": partial_artifacts_before,
        "artifacts": artifacts,
        "artifacts_complete": artifacts_complete,
        "promotion_manifest_exists": promotion_manifest.exists(),
        "log_file": str(log_file),
    }
    if not safe:
        return 3, report
    if not route_success or not artifacts_complete:
        return 2, report
    return 0, report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        code, report = run(args)
    except Exception as exc:
        code = 4
        report = {"result": "failed", "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
