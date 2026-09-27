"""Small deterministic JSON and JSONL file helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import EvaluationError


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EvaluationError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"invalid JSON in {path}: {exc}") from exc


def write_json_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        value,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
            stream.write("\n")
    except FileExistsError as exc:
        raise EvaluationError(f"refusing to overwrite existing output: {path}") from exc
    except OSError as exc:
        raise EvaluationError(f"cannot write {path}: {exc}") from exc


def write_text_new(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except FileExistsError as exc:
        raise EvaluationError(f"refusing to overwrite existing output: {path}") from exc
    except OSError as exc:
        raise EvaluationError(f"cannot write {path}: {exc}") from exc
