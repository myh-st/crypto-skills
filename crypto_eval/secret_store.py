"""OS-backed local credential storage; secret values never reach SQLite, logs, or the browser.

Backends:

- macOS Keychain through ``/usr/bin/security``. Values are passed hex-encoded on the
  interactive tool's stdin, never on a process command line.
- Linux Secret Service through ``secret-tool`` (value on stdin).
- A process-memory store for the session only.

There is intentionally no plaintext-file backend.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .paper_contracts import PaperTradingError, iso_utc


SECRET_SERVICE = "crypto-skills.paper"
SECRET_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
MAX_SECRET_BYTES = 8192
SECURITY_BINARY = "/usr/bin/security"


class SecretStoreError(PaperTradingError):
    """A credential store operation failed; the message never contains the value."""


def validate_secret_id(secret_id: Any) -> str:
    if not isinstance(secret_id, str) or not SECRET_ID.fullmatch(secret_id):
        raise SecretStoreError("secret id must be 1-80 lowercase letters, digits, '.', '_' or '-'")
    return secret_id


def _validate_value(value: Any) -> str:
    if not isinstance(value, str):
        raise SecretStoreError("secret value must be a string")
    value = value.strip()
    if not value or len(value.encode("utf-8")) > MAX_SECRET_BYTES or "\x00" in value:
        raise SecretStoreError("secret value is empty or exceeds the supported size")
    if any(char in value for char in "\r\n"):
        raise SecretStoreError("secret value must be a single line")
    return value


class SecretStore(Protocol):
    backend: str
    persistent: bool

    def available(self) -> bool: ...

    def get(self, secret_id: str) -> str | None: ...

    def set(self, secret_id: str, value: str) -> None: ...

    def delete(self, secret_id: str) -> bool: ...


class MemorySecretStore:
    """Session-only store; values disappear with the process."""

    backend = "session-memory"
    persistent = False

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = threading.Lock()

    def available(self) -> bool:
        return True

    def get(self, secret_id: str) -> str | None:
        with self._lock:
            return self._values.get(validate_secret_id(secret_id))

    def set(self, secret_id: str, value: str) -> None:
        with self._lock:
            self._values[validate_secret_id(secret_id)] = _validate_value(value)

    def delete(self, secret_id: str) -> bool:
        with self._lock:
            return self._values.pop(validate_secret_id(secret_id), None) is not None


class MacKeychainSecretStore:
    """Generic-password items in the user's login Keychain."""

    backend = "macos-keychain"
    persistent = True

    def __init__(self, *, service: str = SECRET_SERVICE, binary: str = SECURITY_BINARY) -> None:
        self.service = service
        self.binary = binary

    def available(self) -> bool:
        return sys.platform == "darwin" and os.path.isfile(self.binary)

    def _run(self, args: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.binary, *args],
                input=stdin,
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            raise SecretStoreError("the OS credential store is unavailable") from None

    def get(self, secret_id: str) -> str | None:
        result = self._run(
            ["find-generic-password", "-a", validate_secret_id(secret_id), "-s", self.service, "-w"]
        )
        if result.returncode == 44:
            return None
        if result.returncode != 0:
            raise SecretStoreError("the OS credential store could not read the credential")
        value = result.stdout.decode("utf-8", "replace").rstrip("\n")
        return value or None

    def set(self, secret_id: str, value: str) -> None:
        account = validate_secret_id(secret_id)
        encoded = _validate_value(value).encode("utf-8").hex()
        command = (
            f'add-generic-password -U -a {account} -s {self.service} '
            f'-l "crypto-skills {account}" -X {encoded}\n'
        ).encode("ascii")
        result = self._run(["-i"], stdin=command)
        if result.returncode != 0 or self.get(account) is None:
            raise SecretStoreError("the OS credential store did not save the credential")

    def delete(self, secret_id: str) -> bool:
        result = self._run(
            ["delete-generic-password", "-a", validate_secret_id(secret_id), "-s", self.service]
        )
        if result.returncode == 44:
            return False
        if result.returncode != 0:
            raise SecretStoreError("the OS credential store could not delete the credential")
        return True


class SecretToolSecretStore:
    """Linux Secret Service via libsecret's ``secret-tool``."""

    backend = "linux-secret-service"
    persistent = True

    def __init__(self, *, service: str = SECRET_SERVICE) -> None:
        self.service = service
        self.binary = shutil.which("secret-tool")

    def available(self) -> bool:
        return sys.platform.startswith("linux") and self.binary is not None

    def _run(self, args: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        if self.binary is None:
            raise SecretStoreError("the OS credential store is unavailable")
        try:
            return subprocess.run(
                [self.binary, *args], input=stdin, capture_output=True, timeout=20, check=False
            )
        except (OSError, subprocess.SubprocessError):
            raise SecretStoreError("the OS credential store is unavailable") from None

    def get(self, secret_id: str) -> str | None:
        result = self._run(
            ["lookup", "service", self.service, "account", validate_secret_id(secret_id)]
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", "replace").rstrip("\n") or None

    def set(self, secret_id: str, value: str) -> None:
        account = validate_secret_id(secret_id)
        result = self._run(
            ["store", "--label", f"crypto-skills {account}", "service", self.service, "account", account],
            stdin=_validate_value(value).encode("utf-8"),
        )
        if result.returncode != 0:
            raise SecretStoreError("the OS credential store did not save the credential")

    def delete(self, secret_id: str) -> bool:
        result = self._run(
            ["clear", "service", self.service, "account", validate_secret_id(secret_id)]
        )
        return result.returncode == 0


def default_secret_store() -> SecretStore:
    for candidate in (MacKeychainSecretStore(), SecretToolSecretStore()):
        if candidate.available():
            return candidate
    return MemorySecretStore()


class CredentialResolver:
    """Resolve a provider/account credential from the OS store, then an env reference.

    Metadata about which source answered is returned; the value itself is only handed to
    the adapter that performs the request.
    """

    def __init__(
        self,
        store: SecretStore | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store if store is not None else default_secret_store()
        self.environ = environ

    def _env(self) -> Mapping[str, str]:
        return os.environ if self.environ is None else self.environ

    def resolve(self, *, secret_id: str | None, env_name: str | None) -> tuple[str | None, str]:
        if secret_id:
            try:
                value = self.store.get(secret_id)
            except SecretStoreError:
                value = None
            if value:
                return value, self.store.backend
        if env_name:
            value = self._env().get(env_name)
            if isinstance(value, str) and value.strip():
                return value.strip(), "environment"
        return None, "missing"

    def status(self, *, secret_id: str | None, env_name: str | None) -> dict[str, Any]:
        value, source = self.resolve(secret_id=secret_id, env_name=env_name)
        return {
            "stored": value is not None,
            "source": source,
            "secret_backend": self.store.backend,
            "secret_backend_persistent": bool(self.store.persistent),
        }


def store_secret(store: SecretStore, secret_id: str, value: str) -> dict[str, Any]:
    """Persist a secret and return only masked metadata."""

    store.set(secret_id, value)
    return {
        "secret_id": validate_secret_id(secret_id),
        "stored": True,
        "backend": store.backend,
        "persistent": bool(store.persistent),
        "stored_at": iso_utc(datetime.now(timezone.utc)),
        "masked": "••••••••",
    }


SENTINEL_FIELDS = ("api_key", "secret", "token", "authorization", "password", "credential_value")


def scrub(text: str, secrets: list[str]) -> str:
    """Replace any known secret occurrence in diagnostic text."""

    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    return text
