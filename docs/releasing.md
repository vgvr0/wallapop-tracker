# Manual release checklist

This project is prepared for local, reproducible artifacts. Publishing is intentionally manual and
is not performed by the repository workflows or the release helper.

Before a release:

- [ ] Update the version in `pyproject.toml` and keep it as the single version source.
- [ ] Confirm the package metadata and README describe the current behavior.
- [ ] Confirm the MIT `LICENSE` file and its metadata are present in both artifacts.
- [ ] Run `python -m pip install -e ".[dev,release]"` in a clean Python 3.12+ environment.
- [ ] Run `ruff check .`.
- [ ] Run `ruff format --check .`.
- [ ] Run `mypy src`.
- [ ] Run `pytest`.
- [ ] Run `python scripts/build_release.py`.
- [ ] Verify the wheel with `python scripts/validate_installed_package.py` from a clean wheel-only environment.
- [ ] Rebuild from the source distribution and repeat the wheel checks.
- [ ] Inspect artifact contents for package files, `py.typed`, `LICENSE`, and accidental local data.
- [ ] Prepare release notes.
- [ ] Create the Git tag manually after review, for example `v1.0.0`.
- [ ] Create the GitHub release manually.
- [ ] Optionally publish to TestPyPI manually after explicit approval:
  `python -m twine upload --repository testpypi dist/*`.
- [ ] Publish to PyPI manually after explicit approval:
  `python -m twine upload dist/*`.

The release helper only cleans local build artifacts, builds the sdist and wheel, runs `twine check`,
and audits their contents. It never uploads, tags, pushes, or creates a GitHub release.
