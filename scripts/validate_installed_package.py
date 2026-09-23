"""Validate a package installed from a wheel, without importing the source tree."""

from __future__ import annotations

import shutil
import subprocess
import sys


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main() -> None:
    run(sys.executable, "-c", "import wallapop_tracker")
    run(
        sys.executable,
        "-c",
        "from wallapop_tracker.api.app import app; assert app.title == 'Wallapop Tracker API'",
    )
    executable = shutil.which("wallapop-track")
    if executable is None:
        raise RuntimeError("wallapop-track entry point is not on PATH")
    for namespace in ("--help", "search --help", "listing --help", "analytics --help"):
        run(executable, *namespace.split())


if __name__ == "__main__":
    main()
