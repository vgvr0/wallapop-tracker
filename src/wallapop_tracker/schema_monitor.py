"""Cheap, value-independent payload schema fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .storage.models import SchemaDriftEventRecord, SchemaObservationRecord


def schema_paths(value: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(f"{path}:object")
            paths.update(schema_paths(child, path))
    elif isinstance(value, list):
        path = f"{prefix}:array"
        paths.add(path)
        for child in value[:1]:
            paths.update(schema_paths(child, prefix + "[]"))
    else:
        paths.add(f"{prefix}:{type(value).__name__}")
    return paths


def schema_fingerprint(value: Any) -> tuple[str, tuple[str, ...]]:
    paths = tuple(sorted(schema_paths(value)))
    signature = hashlib.sha256(json.dumps(paths, separators=(",", ":")).encode()).hexdigest()
    return signature, paths


def schema_diff(previous: set[str], current: set[str]) -> dict[str, list[str]]:
    return {"missing_paths": sorted(previous - current), "new_paths": sorted(current - previous)}


def observe_schema(
    session: Session, source: str, payload: Any, *, observed_at: datetime | None = None
) -> SchemaDriftEventRecord | None:
    signature, paths = schema_fingerprint(payload)
    now = observed_at or datetime.now(UTC)
    observation = session.scalar(
        select(SchemaObservationRecord).where(SchemaObservationRecord.source == source)
    )
    if observation is None:
        session.add(
            SchemaObservationRecord(
                source=source, signature=signature, paths_json=json.dumps(paths), detected_at=now
            )
        )
        return None
    if observation.signature == signature:
        return None
    previous_paths = set(json.loads(observation.paths_json))
    diff = schema_diff(previous_paths, set(paths))
    event: SchemaDriftEventRecord | None = SchemaDriftEventRecord(
        source=source,
        previous_signature=observation.signature,
        current_signature=signature,
        missing_paths_json=json.dumps(diff["missing_paths"]),
        new_paths_json=json.dumps(diff["new_paths"]),
        changed_types_json=json.dumps([]),
        detected_at=now,
    )
    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
    except IntegrityError:
        event = None
    observation.signature, observation.paths_json, observation.detected_at = (
        signature,
        json.dumps(paths),
        now,
    )
    return event


__all__ = ["schema_diff", "schema_fingerprint", "schema_paths"]
