"""Small, non-evaluating parser for local provider environment files."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import MutableMapping

from .paper_contracts import PaperTradingError


MAX_ENV_FILE_BYTES = 64 * 1024
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")


def load_environment_file(
    path: str | Path,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> int:
    """Load plain KEY=value entries without shell parsing or value logging.

    Existing process environment entries always win. This deliberately does
    not support shell commands, variable expansion, `source`, or `eval`.
    """

    file_path = Path(path)
    target = os.environ if environ is None else environ
    try:
        size = file_path.stat().st_size
        if size > MAX_ENV_FILE_BYTES:
            raise PaperTradingError("local environment file exceeds 64 KiB")
        source = file_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return 0
    except PaperTradingError:
        raise
    except (OSError, UnicodeError):
        raise PaperTradingError("local environment file could not be read") from None

    seen: set[str] = set()
    loaded = 0
    for line_number, source_line in enumerate(source.splitlines(), 1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not ENV_NAME.fullmatch(name):
            raise PaperTradingError(
                f"local environment file has an invalid entry at line {line_number}"
            )
        if name in seen:
            raise PaperTradingError(
                f"local environment file repeats a variable at line {line_number}"
            )
        seen.add(name)
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise PaperTradingError(
                    f"local environment file has an unterminated value at line {line_number}"
                )
            value = value[1:-1]
        if "\x00" in value:
            raise PaperTradingError(
                f"local environment file has an invalid value at line {line_number}"
            )
        if name not in target:
            target[name] = value
            loaded += 1
    return loaded
