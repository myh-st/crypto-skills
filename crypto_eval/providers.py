"""Read-only archived-data provider interface for the evaluation harness.

Providers normalize archived or prospective observations into candidate
bundles. This evaluation adapter intentionally ships no credentialed market
API client; the separate ``paper_market`` module contains the opt-in public
USD-M futures feed used only by the local PAPER runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .contracts import EvaluationError
from .io import read_json


@runtime_checkable
class ReadOnlyMarketDataProvider(Protocol):
    """Interface for providers that can only return normalized market evidence."""

    @property
    def provider_id(self) -> str:
        """Stable provider/archive identifier; never include credentials."""

    def load_candidates(self) -> dict[str, Any]:
        """Return a normalized point-in-time candidate bundle."""


class JsonArchiveProvider:
    """Read-only adapter for a locally archived normalized candidate bundle."""

    def __init__(self, path: Path, provider_id: str = "json-archive") -> None:
        self._path = path
        self._provider_id = provider_id

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def load_candidates(self) -> dict[str, Any]:
        bundle = read_json(self._path)
        if not isinstance(bundle, dict):
            raise EvaluationError(f"candidate archive must be a JSON object: {self._path}")
        return bundle
