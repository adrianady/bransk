import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/health":
            body = b'{"status":"ok"}'
        elif self.path == "/api/auth/status":
            required = getattr(self.server, "auth_required", False)
            body = b'{"required":' + (b"true" if required else b"false") + b"}"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("bash") or not shutil.which("curl"),
    reason="start.sh requires a POSIX host with bash and curl",
)
def test_network_start_does_not_treat_loopback_server_as_remote_ready(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ss = fake_bin / "ss"
    fake_ss.write_text(
        "#!/bin/sh\n"
        f"printf 'LISTEN 0 128 127.0.0.1:{server.server_address[1]} 127.0.0.1:*\\n'\n",
        encoding="utf-8",
    )
    fake_ss.chmod(0o755)
    env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
    try:
        result = subprocess.run(
            ["bash", str(ROOT / "start.sh"), "-n", str(server.server_address[1])],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

        assert result.returncode != 0
        assert "localhost" in (result.stdout + result.stderr).lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("bash") or not shutil.which("curl"),
    reason="start.sh requires a POSIX host with bash and curl",
)
def test_network_start_refuses_an_existing_exposed_server_without_auth(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.auth_required = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ss = fake_bin / "ss"
    fake_ss.write_text(
        "#!/bin/sh\n"
        f"printf 'LISTEN 0 128 0.0.0.0:{server.server_address[1]} 0.0.0.0:*\\n'\n",
        encoding="utf-8",
    )
    fake_ss.chmod(0o755)
    env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
    try:
        result = subprocess.run(
            ["bash", str(ROOT / "start.sh"), "-n", str(server.server_address[1])],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

        assert result.returncode != 0
        assert "token" in (result.stdout + result.stderr).lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("bash") or not shutil.which("curl"),
    reason="start.sh requires a POSIX host with bash and curl",
)
def test_network_start_accepts_an_existing_exposed_server_with_auth(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    server.auth_required = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ss = fake_bin / "ss"
    fake_ss.write_text(
        "#!/bin/sh\n"
        f"printf 'LISTEN 0 128 0.0.0.0:{server.server_address[1]} 0.0.0.0:*\\n'\n",
        encoding="utf-8",
    )
    fake_ss.chmod(0o755)
    env = {"HOME": os.environ.get("HOME", "/tmp"), "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"}
    try:
        result = subprocess.run(
            ["bash", str(ROOT / "start.sh"), "-n", str(server.server_address[1])],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

        assert result.returncode == 0
        assert "rulează deja" in result.stdout.lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
