"""Build and validate local release artifacts without publishing them."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def clean_artifacts() -> None:
    for path in (ROOT / "build", DIST):
        if path.exists():
            shutil.rmtree(path)
    for path in (ROOT, ROOT / "src"):
        for egg_info in path.glob("*.egg-info"):
            if egg_info.is_dir():
                shutil.rmtree(egg_info)


def artifact_members(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    with tarfile.open(path, "r:gz") as archive:
        return archive.getnames()


def validate_artifacts() -> None:
    artifacts = sorted(DIST.iterdir())
    if len(artifacts) != 2:
        raise RuntimeError(f"expected exactly one wheel and one sdist, found: {artifacts}")
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(f"expected one .whl and one .tar.gz, found: {artifacts}")

    for artifact in artifacts:
        members = artifact_members(artifact)
        for member in members:
            normalized = member.lower().replace("\\", "/")
            basename = normalized.rsplit("/", 1)[-1]
            forbidden = (
                basename in {".env", "secrets.json"}
                or basename.endswith((".db", ".sqlite", ".sqlite3"))
                or "/raw/" in f"/{normalized}/"
                or "/.git/" in f"/{normalized}/"
            )
            if forbidden:
                raise RuntimeError(f"forbidden path in {artifact.name}: {member}")

    wheel_members = artifact_members(wheels[0])
    if not any(member.startswith("wallapop_tracker/") for member in wheel_members):
        raise RuntimeError("wheel does not contain the wallapop_tracker package")
    if "wallapop_tracker/py.typed" not in wheel_members:
        raise RuntimeError("wheel is missing wallapop_tracker/py.typed")
    if not any(member.lower().endswith("license") for member in wheel_members):
        raise RuntimeError("wheel is missing the LICENSE metadata file")
    for expected in ("pyproject.toml", "README.md", "src/wallapop_tracker"):
        if not any(expected in member for member in artifact_members(sdists[0])):
            raise RuntimeError(f"sdist is missing expected content: {expected}")
    if not any(member.lower().endswith("/license") for member in artifact_members(sdists[0])):
        raise RuntimeError("sdist is missing LICENSE")

    print(f"wheel: {wheels[0].name}")
    print(f"sdist: {sdists[0].name}")


def main() -> None:
    clean_artifacts()
    run(sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(DIST))
    run(sys.executable, "-m", "twine", "check", *[str(path) for path in sorted(DIST.iterdir())])
    validate_artifacts()


if __name__ == "__main__":
    main()
