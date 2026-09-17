"""Compare the observed JSON schema of two API fixtures, ignoring values."""

import json
import sys
from pathlib import Path
from typing import Any


def schema(value: Any, path: str = "$") -> dict[str, str]:
    if value is None:
        return {path: "null"}
    if isinstance(value, bool):
        return {path: "bool"}
    if isinstance(value, int) and not isinstance(value, bool):
        return {path: "int"}
    if isinstance(value, float):
        return {path: "float"}
    if isinstance(value, str):
        return {path: "str"}
    if isinstance(value, list):
        result = {path: "list"}
        for item in value[:1]:
            result.update(schema(item, f"{path}[]"))
        return result
    if isinstance(value, dict):
        result = {path: "object"}
        for key, item in sorted(value.items()):
            result.update(schema(item, f"{path}.{key}"))
        return result
    return {path: type(value).__name__}


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python scripts/compare_api_fixture.py OLD.json NEW.json")
        return 2
    old_path, new_path = map(Path, sys.argv[1:])
    old = schema(json.loads(old_path.read_text(encoding="utf-8")))
    new = schema(json.loads(new_path.read_text(encoding="utf-8")))
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    print("ADDED:")
    for path in added:
        print(f"+ {path} ({new[path]})")
    print("REMOVED:")
    for path in removed:
        print(f"- {path} ({old[path]})")
    print("TYPE_CHANGED:")
    for path in changed:
        print(f"~ {path}\n  {old[path]} -> {new[path]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
