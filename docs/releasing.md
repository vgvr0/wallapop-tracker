# Release checklist

Publishing to PyPI is NOT automatic.

For a GitHub release:

1. Update the version in `pyproject.toml`.
2. Merge the change to `master`.
3. Validate `master`.
4. Create a tag in the form `vX.Y.Z` matching the `pyproject.toml` version.
5. Push the tag:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

6. GitHub Actions builds and validates the wheel and source distribution.
7. GitHub Actions creates the GitHub Release and attaches the wheel and sdist.
8. PyPI remains a manual operation after explicit approval.

The release workflow runs only for `v*` tags. It checks that the tag and project
version match, runs all quality gates, validates installation from a clean wheel,
rebuilds and validates a wheel from the sdist, and uploads only the wheel and sdist.
If a release already exists for the tag, a rerun replaces its two assets safely.

## Manual release checklist

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
