"""Reproducible local Docker/Compose smoke test; never performs live scraping."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("$", " ".join(args), flush=True)
    return subprocess.run(args, cwd=ROOT, text=True, check=check)


def endpoint(path: str, expected: int = 200) -> None:
    deadline = time.monotonic() + 60
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:8000{path}", timeout=3) as response:
                if response.status != expected:
                    raise RuntimeError(f"{path}: HTTP {response.status}, expected {expected}")
                return
        except (OSError, URLError) as exc:
            last_error = str(exc)
            time.sleep(2)
    raise RuntimeError(f"{path} did not return HTTP {expected}: {last_error}")


def main() -> int:
    if shutil.which("docker") is None:
        print("docker is not available", file=sys.stderr)
        return 2
    run("docker", "compose", "config")
    run("docker", "build", "--no-cache", "-t", "wallapop-tracker:test", ".")
    run("docker", "compose", "up", "-d", "--build")
    try:
        for path in ("/health", "/ready", "/metrics", "/docs"):
            endpoint(path)
        run("docker", "compose", "exec", "-T", "tracker", "wallapop-track", "--help")
        run("docker", "compose", "exec", "-T", "tracker", "wallapop-track", "list")
        run("docker", "compose", "exec", "-T", "tracker", "alembic", "current")

        ps = subprocess.run(
            ("docker", "compose", "ps", "--format", "json"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        services = [json.loads(line) for line in ps.stdout.splitlines() if line.strip()]
        running = {
            item.get("Service")
            for item in services
            if item.get("State", "").lower() in {"running", "up"}
        }
        if not {"postgres", "tracker"}.issubset(running):
            raise RuntimeError(f"critical services are not running: {running}")

        run("docker", "compose", "down")
        run("docker", "compose", "up", "-d")
        endpoint("/ready")
        run("docker", "compose", "exec", "-T", "tracker", "alembic", "current")
        logs = subprocess.run(
            ("docker", "compose", "logs", "--no-color"),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        if "Traceback (most recent call last)" in logs.stdout:
            raise RuntimeError("Compose logs contain a traceback")
        return 0
    finally:
        run("docker", "compose", "down", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
