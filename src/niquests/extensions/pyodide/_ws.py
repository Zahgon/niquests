from __future__ import annotations

import typing

from js import Promise  # type: ignore[import]
from pyodide.ffi import create_proxy, run_sync  # type: ignore[import]

try:
    from ...packages.urllib3.contrib.webextensions.ws import WebSocketExtensionFromHTTP
except ImportError:
    from ...packages.urllib3.contrib.webextensions.raw import RawExtensionFromHTTP as WebSocketExtensionFromHTTP

try:
    from js import WebSocket as JSWebSocket  # type: ignore[import]
except ImportError:
    JSWebSocket = None

if typing.TYPE_CHECKING:
    from ...packages.urllib3 import HTTPResponse


class PyodideWebSocketExtension(WebSocketExtensionFromHTTP):
    """WebSocket extension for Pyodide using the browser's native WebSocket API.

    Synchronous via JSPI (run_sync). Uses JS Promises for signaling instead of
    asyncio primitives, because run_sync bypasses the asyncio event loop.
    Messages from JS callbacks are buffered in a list and delivered via
    next_payload() which blocks via run_sync on a JS Promise.
    """

    def __init__(self, url: str) -> None:
        if JSWebSocket is None:  # Defensive: depends on JS runtime
            raise OSError(
                "WebSocket is not available in this JavaScript runtime. "
                "Browser environment required (not supported in Node.js)."
            )

        self._closed = False
        self._pending: list[str | bytes | None] = []
        self._waiting_resolve: typing.Any = None
        self._last_msg: str | bytes | None = None
        self._proxies: list[typing.Any] = []

        # Create browser WebSocket
        self._ws = JSWebSocket.new(url)
        self._ws.binaryType = "arraybuffer"

        # Open/error signaling via a JS Promise (not asyncio.Future,
        # because run_sync/JSPI does not drive the asyncio event loop).
        _open_state: dict[str, typing.Any] = {
            "resolve": None,
            "reject": None,
        }

        def _open_executor(resolve: typing.Any, reject: typing.Any) -> None:
            pass

        exec_proxy = create_proxy(_open_executor)
        open_promise = Promise.new(exec_proxy)
        self._proxies.append(exec_proxy)

        # JS→Python callbacks: invoked by the browser event loop, not Python call
        # frames, so coverage cannot trace into them.
        def _onopen(event: typing.Any) -> None:  # Defensive: JS callback
            pass

        def _onerror(event: typing.Any) -> None:  # Defensive: JS callback
            pass

        def _onmessage(event: typing.Any) -> None:  # Defensive: JS callback
            pass

        def _onclose(event: typing.Any) -> None:  # Defensive: JS callback
            pass

        # Create proxies so JS can call these Python functions
        for name, fn in [
            ("onopen", _onopen),
            ("onerror", _onerror),
            ("onmessage", _onmessage),
            ("onclose", _onclose),
        ]:
            proxy = create_proxy(fn)
            self._proxies.append(proxy)
            setattr(self._ws, name, proxy)

        # Block until the connection is open (or error rejects the promise)
        run_sync(open_promise)

    def start(self, response: HTTPResponse) -> None:
        raise NotImplementedError

    @property
    def closed(self) -> bool:
        pass

    def next_payload(self) -> str | bytes | None:
        """Block (via JSPI) until the next message arrives.
        Returns None when the remote end closes the connection."""
        if self._closed:  # Defensive: caller should not call after close
            raise OSError("The WebSocket extension is closed")

        # Drain from buffer first
        if self._pending:
            msg = self._pending.pop(0)
            if msg is None:  # Defensive: serverinitiated close sentinel
                self._closed = True
            return msg

        # Wait for the next message via a JS Promise
        def _executor(resolve: typing.Any, reject: typing.Any) -> None:
            pass

        exec_proxy = create_proxy(_executor)
        promise = Promise.new(exec_proxy)
        run_sync(promise)
        exec_proxy.destroy()

        msg = self._last_msg
        if msg is None:  # Defensive: server-initiated close sentinel
            self._closed = True
        return msg

    def send_payload(self, buf: str | bytes) -> None:
        """Send a message over the WebSocket."""
        if self._closed:  # Defensive: caller should not call after close
            raise OSError("The WebSocket extension is closed")

        if isinstance(buf, (bytes, bytearray)):
            from js import Uint8Array  # type: ignore[import]

            self._ws.send(Uint8Array.new(buf))
        else:
            self._ws.send(buf)

    def ping(self) -> None:
        """No-op — browser WebSocket handles ping/pong at protocol level."""
        pass

    def close(self) -> None:
        """Close the WebSocket and clean up proxies."""
        if self._closed:  # Defensive: idempotent close
            return

        self._closed = True

        try:
            self._ws.close()
        except Exception:  # Defensive: suppress JS errors on teardown
            pass

        for proxy in self._proxies:
            try:
                proxy.destroy()
            except Exception:  # Defensive: suppress JS errors on teardown
                pass
        self._proxies.clear()
