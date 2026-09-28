"""Minimal RFC 6455 WebSocket client (TLS, text frames, ping/pong) using only the stdlib.

Only the client side of the protocol needed for exchange market-data streams is
implemented: client-masked frames, fragmented-message reassembly, control frames,
and a bounded message size. No extensions or compression are negotiated.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import urllib.parse
from typing import Callable


GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocketError(OSError):
    """Protocol or transport failure; never includes payload contents."""


class WebSocketClosed(WebSocketError):
    pass


def _mask(payload: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))


def encode_frame(opcode: int, payload: bytes, *, mask_key: bytes | None = None) -> bytes:
    key = mask_key if mask_key is not None else os.urandom(4)
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + key + _mask(payload, key)


class WebSocketConnection:
    def __init__(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        socket_factory: Callable[[str, int, float], socket.socket] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"wss", "ws"} or not parsed.hostname:
            raise WebSocketError("WebSocket URL must use ws:// or wss://")
        self.url = url
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        self.path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        self.secure = parsed.scheme == "wss"
        self.timeout = timeout
        self._socket_factory = socket_factory
        self._ssl_context = ssl_context
        self._sock: socket.socket | None = None
        self._buffer = b""
        self.closed = False

    def connect(self) -> "WebSocketConnection":
        if self._socket_factory is not None:
            sock = self._socket_factory(self.host, self.port, self.timeout)
        else:
            raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
            if self.secure:
                context = self._ssl_context or ssl.create_default_context()
                sock = context.wrap_socket(raw, server_hostname=self.host)
            else:
                sock = raw
        sock.settimeout(self.timeout)
        self._sock = sock
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        default_port = 443 if self.secure else 80
        host_header = self.host if self.port == default_port else f"{self.host}:{self.port}"
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "User-Agent: crypto-skills-paper/1.0\r\n"
            "\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise WebSocketError("WebSocket handshake closed early")
            response += chunk
            if len(response) > 65536:
                raise WebSocketError("WebSocket handshake response is too large")
        head, _, rest = response.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        if not lines or " 101 " not in f"{lines[0]} ":
            raise WebSocketError("WebSocket upgrade was rejected")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise WebSocketError("WebSocket accept key mismatch")
        self._buffer = rest
        return self

    def _recv_exact(self, size: int) -> bytes:
        assert self._sock is not None
        while len(self._buffer) < size:
            chunk = self._sock.recv(max(4096, size - len(self._buffer)))
            if not chunk:
                raise WebSocketClosed("WebSocket connection closed")
            self._buffer += chunk
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._recv_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._recv_exact(8))
        if length > MAX_MESSAGE_BYTES:
            raise WebSocketError("WebSocket frame exceeds the supported size")
        key = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length) if length else b""
        if key is not None:
            payload = _mask(payload, key)
        return fin, opcode, payload

    def send_text(self, text: str) -> None:
        self._send(OP_TEXT, text.encode("utf-8"))

    def _send(self, opcode: int, payload: bytes) -> None:
        if self._sock is None or self.closed:
            raise WebSocketClosed("WebSocket is not connected")
        self._sock.sendall(encode_frame(opcode, payload))

    def recv(self) -> str | None:
        """Return the next text message; None on a read timeout (caller may heartbeat)."""

        fragments: list[bytes] = []
        message_opcode: int | None = None
        while True:
            try:
                fin, opcode, payload = self._read_frame()
            except (socket.timeout, TimeoutError):
                if fragments:
                    raise WebSocketError("WebSocket message fragment timed out") from None
                return None
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.closed = True
                try:
                    self._send_close_ack(payload)
                finally:
                    raise WebSocketClosed("WebSocket closed by peer")
            if opcode in {OP_TEXT, OP_BINARY}:
                message_opcode = opcode
                fragments = [payload]
            elif opcode == OP_CONTINUATION and message_opcode is not None:
                fragments.append(payload)
            else:
                raise WebSocketError("WebSocket protocol error")
            if sum(len(item) for item in fragments) > MAX_MESSAGE_BYTES:
                raise WebSocketError("WebSocket message exceeds the supported size")
            if fin:
                data = b"".join(fragments)
                return data.decode("utf-8", "replace")

    def _send_close_ack(self, payload: bytes) -> None:
        if self._sock is not None:
            try:
                self._sock.sendall(encode_frame(OP_CLOSE, payload[:2]))
            except OSError:
                pass

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            if not self.closed:
                self._sock.sendall(encode_frame(OP_CLOSE, struct.pack("!H", 1000)))
        except OSError:
            pass
        finally:
            self.closed = True
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
