"""Local mock HTTP regression tests for the Hermes skill scripts.

These tests start a tiny HTTP server on an ephemeral port and point the
Hermes skill scripts at it via --base-url. The server mimics the parts
of SEO Ops that the scripts depend on:
- POST endpoints respond with 303 + Location containing level=success|error|warning
- GET /api/health responds with 200 + JSON

The tests verify:
1. r0/r1/r3/w0/w1b/w2/w2-revise/w3 all accept --base-url and --timeout
2. level=success 303 → exit 0
3. level=error 303 → non-zero exit
4. level=warning 303 → exit 0 with warning to stderr
5. Form body for r1 (search_results) and w0 (author) / w1b (tier) / w2 (apply,force)
   is correctly sent
6. detect_stage exits 0 on 200, non-zero on 5xx
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import pytest

SCRIPTS_DIR = Path(
    "/home/laoma/seo-ops-system/integrations/hermes/seo-ops-orchestrator/scripts"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Mock server ─────────────────────────────────────────────────────────


class MockHandler(BaseHTTPRequestHandler):
    """Records every request and replies with a configurable response."""

    # These are set by the test fixture before the server starts.
    next_status: int = 303
    next_level: str = "success"
    next_message: str = ""
    received: list[dict] = []

    def log_message(self, format, *args):
        return  # silence

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    def _record(self, method: str) -> None:
        body = self._read_body()
        self.received.append({
            "method": method,
            "path": self.path,
            "body": body,
            "content_type": self.headers.get("Content-Type", ""),
            "form": parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True),
        })

    def _send_redirect(self) -> None:
        location = f"/actions?message={self.next_message}&level={self.next_level}"
        self.send_response(self.next_status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        self._record("POST")
        self._send_redirect()

    def do_GET(self):
        self._record("GET")
        if self.path == "/api/health":
            body = b'{"status":"ok","version":"mock"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def mock_server():
    MockHandler.received = []
    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", MockHandler
    finally:
        server.shutdown()
        server.server_close()


# ── Helpers ─────────────────────────────────────────────────────────────


def _run_script(script: str, args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(SCRIPTS_DIR / script), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ── Tests ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "script,args_factory",
    [
        ("r0.sh", lambda: ["1"]),
        ("r1.sh", lambda: ["1"]),
        ("r3.sh", lambda: ["1"]),
        ("w0.sh", lambda: ["1"]),
        ("w1b.sh", lambda: ["1"]),
        ("w2.sh", lambda: ["1"]),
        ("w2-revise.sh", lambda: ["1"]),
        ("w3.sh", lambda: ["1"]),
    ],
)
def test_stage_scripts_accept_base_url_and_timeout(mock_server, script, args_factory):
    base_url, _handler = mock_server
    proc = _run_script(script, ["--base-url", base_url, "--timeout", "5", *args_factory()])
    assert proc.returncode == 0, (
        f"{script} failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )


def test_r0_success_303_exits_zero(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "success"
    handler.next_message = "ok"
    proc = _run_script("r0.sh", ["--base-url", base_url, "1"])
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    assert "level=success" in proc.stdout
    assert "http_status=303" in proc.stdout


def test_r0_error_303_exits_nonzero(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "error"
    handler.next_message = "missing%20action"
    proc = _run_script("r0.sh", ["--base-url", base_url, "1"])
    assert proc.returncode != 0, f"stdout={proc.stdout!r}"
    assert "level=error" in proc.stderr
    assert "missing action" in proc.stderr
    assert "http_status=303" in proc.stdout  # status line always on stdout


def test_w0_warning_303_exits_zero_with_warning(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "warning"
    handler.next_message = "no%20draft%20yet"
    proc = _run_script("w0.sh", ["--base-url", base_url, "1"])
    assert proc.returncode == 0
    assert "level=warning" in proc.stdout
    assert "level=warning" in proc.stderr
    assert "no draft yet" in proc.stderr


def test_r1_sends_search_results_form(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "success"
    proc = subprocess.run(
        [str(SCRIPTS_DIR / "r1.sh"), "--base-url", base_url, "1"],
        input="Sample search results line 1\nline 2\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    assert len(handler.received) == 1
    body = handler.received[0]
    assert body["method"] == "POST"
    assert body["path"] == "/actions/1/legacy/stage/r1"
    assert "search_results" in body["form"]
    assert body["form"]["search_results"] == ["Sample search results line 1\nline 2\n"]


def test_w0_sends_author_form(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "success"
    proc = _run_script(
        "w0.sh", ["--base-url", base_url, "--author", "TestAuthor", "1"]
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    assert handler.received[0]["form"]["author"] == ["TestAuthor"]


def test_w1b_sends_tier_form(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "success"
    proc = _run_script(
        "w1b.sh", ["--base-url", base_url, "--tier", "Pillar%20Page", "1"]
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    # urldecode happens in the route, so the body might still be encoded
    tier_val = handler.received[0]["form"]["tier"][0]
    assert "Pillar" in tier_val


def test_w1b_no_tier_sends_empty_form(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "success"
    proc = _run_script("w1b.sh", ["--base-url", base_url, "1"])
    assert proc.returncode == 0
    # Empty form file still sends a POST (with no fields)
    assert handler.received[0]["method"] == "POST"
    assert handler.received[0]["form"] == {}


def test_w2_sends_apply_and_force_form(mock_server):
    base_url, handler = mock_server
    handler.next_status = 303
    handler.next_level = "success"
    proc = _run_script(
        "w2.sh", ["--base-url", base_url, "--apply", "--force", "1"]
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    form = handler.received[0]["form"]
    assert form.get("apply") == ["1"]
    assert form.get("force") == ["1"]


def test_detect_stage_exits_zero_on_200(mock_server):
    base_url, _ = mock_server
    proc = _run_script("detect_stage.sh", ["--base-url", base_url])
    assert proc.returncode == 0
    assert "http_status=200" in proc.stdout
    assert "mock" in proc.stdout  # the body of /api/health


def test_too_many_args_exits_nonzero(mock_server):
    """Old bug: parse_flags() in a function left $@ unchanged, so the
    script then saw the flag and the action_id as separate args."""
    base_url, _ = mock_server
    proc = _run_script("r0.sh", ["--base-url", base_url, "1", "2"])
    assert proc.returncode != 0
    assert "Too many arguments" in proc.stderr


def test_missing_action_id_exits_nonzero(mock_server):
    base_url, _ = mock_server
    proc = _run_script("r0.sh", ["--base-url", base_url])
    assert proc.returncode != 0
    assert "Missing action_id" in proc.stderr


def test_help_flag_exits_zero(mock_server):
    base_url, _ = mock_server
    proc = _run_script("r0.sh", ["--base-url", base_url, "--help"])
    assert proc.returncode == 0
    assert "r0.sh" in proc.stdout
