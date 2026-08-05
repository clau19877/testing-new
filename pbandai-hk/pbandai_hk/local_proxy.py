from __future__ import annotations

import base64
import select
import socket
import threading
from typing import List, Optional

from .logging_utils import get_logger
from .proxy_util import ProxyConfig, redact_proxy

logger = get_logger("local_proxy")

# Keep bridges alive for the lifetime of browser sessions.
_ACTIVE_BRIDGES: List["LocalAuthProxy"] = []
_LOCK = threading.Lock()


class LocalAuthProxy:
    """Local HTTP proxy that injects Proxy-Authorization to an upstream proxy.

    Chrome cannot natively use user:pass in --proxy-server, and proxy-auth
    extensions often fail on modern Chrome. Point Chromium at this local
    listener instead (no auth popup).
    """

    def __init__(self, upstream: ProxyConfig) -> None:
        if not upstream.username:
            raise ValueError("LocalAuthProxy requires upstream proxy credentials")
        self.upstream = upstream
        self._auth = base64.b64encode(
            f"{upstream.username}:{upstream.password or ''}".encode("utf-8")
        ).decode("ascii")
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self.port = 0
        self._stopped = threading.Event()

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(64)
        sock.settimeout(1.0)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._thread = threading.Thread(
            target=self._serve,
            name=f"proxy-bridge-{self.port}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "local auth proxy started local=%s upstream=%s",
            self.local_url,
            redact_proxy(upstream_raw(self.upstream)),
        )
        return self.local_url

    def stop(self) -> None:
        self._stopped.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stopped.is_set():
            try:
                client, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle_client,
                args=(client,),
                daemon=True,
            ).start()

    def _handle_client(self, client: socket.socket) -> None:
        upstream_sock: Optional[socket.socket] = None
        try:
            client.settimeout(30)
            data = _recv_headers(client)
            if not data:
                return
            upstream_sock = socket.create_connection(
                (self.upstream.host, self.upstream.port),
                timeout=30,
            )
            upstream_sock.settimeout(30)
            upstream_sock.sendall(self._inject_auth(data))

            first = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            if first.upper().startswith("CONNECT "):
                resp = _recv_headers(upstream_sock)
                if not resp:
                    return
                client.sendall(resp)
                status = resp.split(b"\r\n", 1)[0]
                if b" 200 " not in status and not status.endswith(b" 200"):
                    # still forward body if any, then close
                    return
                _tunnel(client, upstream_sock)
            else:
                _tunnel(client, upstream_sock)
        except Exception as exc:  # noqa: BLE001
            logger.debug("proxy bridge client error: %s", exc)
        finally:
            try:
                client.close()
            except OSError:
                pass
            if upstream_sock is not None:
                try:
                    upstream_sock.close()
                except OSError:
                    pass

    def _inject_auth(self, request: bytes) -> bytes:
        if b"\r\n\r\n" not in request:
            return request
        head, body = request.split(b"\r\n\r\n", 1)
        lines = head.split(b"\r\n")
        if not lines:
            return request
        kept = [lines[0]]
        for line in lines[1:]:
            if line.lower().startswith(b"proxy-authorization:"):
                continue
            kept.append(line)
        kept.insert(1, f"Proxy-Authorization: Basic {self._auth}".encode("ascii"))
        return b"\r\n".join(kept) + b"\r\n\r\n" + body


def upstream_raw(proxy: ProxyConfig) -> str:
    return proxy.raw


def start_local_auth_proxy(proxy: ProxyConfig) -> LocalAuthProxy:
    bridge = LocalAuthProxy(proxy)
    bridge.start()
    with _LOCK:
        _ACTIVE_BRIDGES.append(bridge)
    return bridge


def _recv_headers(sock: socket.socket, limit: int = 1024 * 1024) -> bytes:
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            break
    return buf


def _tunnel(a: socket.socket, b: socket.socket) -> None:
    sockets = [a, b]
    try:
        while True:
            readable, _, errored = select.select(sockets, [], sockets, 120)
            if errored or not readable:
                break
            for sock in readable:
                other = b if sock is a else a
                try:
                    data = sock.recv(65536)
                except OSError:
                    return
                if not data:
                    return
                try:
                    other.sendall(data)
                except OSError:
                    return
    finally:
        for sock in (a, b):
            try:
                sock.close()
            except OSError:
                pass
