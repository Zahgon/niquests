"""
requests.auth
~~~~~~~~~~~~~

This module contains the authentication handlers for Requests.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import re
import time
import typing
from base64 import b64encode
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ._compat import iscoroutinefunction
from .cookies import extract_cookies_to_jar
from .utils import parse_dict_header

if typing.TYPE_CHECKING:
    from .models import PreparedRequest

CONTENT_TYPE_FORM_URLENCODED: str = "application/x-www-form-urlencoded"
CONTENT_TYPE_MULTI_PART: str = "multipart/form-data"


def _basic_auth_str(username: str | bytes, password: str | bytes) -> str:
    """Returns a Basic Auth string."""

    if isinstance(username, str):
        username = username.encode("utf-8")

    if isinstance(password, str):
        password = password.encode("utf-8")

    authstr = "Basic " + b64encode(b":".join((username, password))).strip().decode()

    return authstr


class AsyncAuthBase:
    """Base class that all asynchronous auth implementations derive from"""

    async def __call__(self, r: PreparedRequest) -> PreparedRequest:
        raise NotImplementedError("Auth hooks must be callable.")


class AuthBase:
    """Base class that all synchronous auth implementations derive from"""

    def __call__(self, r: PreparedRequest) -> PreparedRequest:
        raise NotImplementedError("Auth hooks must be callable.")


class BearerTokenAuth(AuthBase):
    """Simple token injection in Authorization header"""

    def __init__(self, token: str):
        self.token = token

    def __eq__(self, other) -> bool:
        return self.token == getattr(other, "token", None)

    def __ne__(self, other) -> bool:
        return not self == other

    def __call__(self, r):
        detect_token_type: list[str] = self.token.split(" ", maxsplit=1)

        if len(detect_token_type) == 1:
            r.headers["Authorization"] = f"Bearer {self.token}"
        else:
            r.headers["Authorization"] = self.token

        return r


class HTTPBasicAuth(AuthBase):
    """Attaches HTTP Basic Authentication to the given Request object."""

    def __init__(self, username: str | bytes, password: str | bytes):
        self.username = username
        self.password = password

    def __eq__(self, other) -> bool:
        return all(
            [
                self.username == getattr(other, "username", None),
                self.password == getattr(other, "password", None),
            ]
        )

    def __ne__(self, other) -> bool:
        return not self == other

    def __call__(self, r):
        r.headers["Authorization"] = _basic_auth_str(self.username, self.password)
        return r


class HTTPProxyAuth(HTTPBasicAuth):
    """Attaches HTTP Proxy Authentication to a given Request object."""

    def __call__(self, r):
        r.headers["Proxy-Authorization"] = _basic_auth_str(self.username, self.password)
        return r


@dataclass
class DigestAuthState:
    """Container for digest auth state per task/thread"""

    init: bool = False
    last_nonce: str = ""
    nonce_count: int = 0
    chal: typing.Mapping[str, str | None] = field(default_factory=dict)
    pos: int | None = None
    num_401_calls: int | None = None


class HTTPDigestAuth(AuthBase):
    """Attaches HTTP Digest Authentication to the given Request object."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        # Keep state in per-thread local storage
        self._thread_local: contextvars.ContextVar[DigestAuthState] = contextvars.ContextVar("digest_auth_state")

    def init_per_thread_state(self) -> None:
        # Ensure state is initialized just once per-thread
        pass

    def build_digest_header(self, method: str, url: str) -> str | None:
        pass

    def handle_redirect(self, r, **kwargs) -> None:
        """Reset num_401_calls counter on redirects."""
        pass

    async def async_handle_401(self, r, **kwargs):
        """
        Takes the given response and tries digest-auth, if needed (async version).

        :rtype: requests.Response
        """
        pass

    def handle_401(self, r, **kwargs):
        """
        Takes the given response and tries digest-auth, if needed.

        :rtype: requests.Response
        """
        pass

    def __call__(self, r):
        # Initialize per-thread state, if needed
        self.init_per_thread_state()
        state = self._thread_local.get(None)
        assert state is not None
        # If we have a saved nonce, skip the 401
        if state.last_nonce:
            r.headers["Authorization"] = self.build_digest_header(r.method, r.url)
        try:
            state.pos = r.body.tell()
        except AttributeError:
            # In the case of HTTPDigestAuth being reused and the body of
            # the previous request was a file-like object, pos has the
            # file position of the previous body. Ensure it's set to
            # None.
            state.pos = None
        # Register sync hooks only - use AsyncHTTPDigestAuth for async sessions
        r.register_hook("response", self.handle_401)
        r.register_hook("response", self.handle_redirect)
        state.num_401_calls = 1

        return r

    def __eq__(self, other) -> bool:
        return all(
            [
                self.username == getattr(other, "username", None),
                self.password == getattr(other, "password", None),
            ]
        )

    def __ne__(self, other) -> bool:
        return not self == other


class AsyncHTTPDigestAuth(HTTPDigestAuth, AsyncAuthBase):
    """Async version of HTTPDigestAuth for use with AsyncSession.

    Attaches HTTP Digest Authentication to the given Request object and handles
    401 responses asynchronously.

    Example usage::

        >>> import niquests
        >>> auth = niquests.auth.AsyncHTTPDigestAuth('user', 'pass')
        >>> async with niquests.AsyncSession() as session:
        ...     r = await session.get('https://httpbin.org/digest-auth/auth/user/pass', auth=auth)
        ...     print(r.status_code)
        200
    """

    async def __call__(self, r):
        # Initialize per-thread state, if needed
        self.init_per_thread_state()
        state = self._thread_local.get(None)
        assert state is not None
        # If we have a saved nonce, skip the 401
        if state.last_nonce:
            r.headers["Authorization"] = self.build_digest_header(r.method, r.url)
        try:
            if iscoroutinefunction(r.body.tell):
                state.pos = await r.body.tell()
            else:
                state.pos = r.body.tell()

        except AttributeError:
            # In the case of AsyncHTTPDigestAuth being reused and the body of
            # the previous request was a file-like object, pos has the
            # file position of the previous body. Ensure it's set to
            # None.
            state.pos = None
        # Register async hooks only
        r.register_hook("response", self.async_handle_401)
        r.register_hook("response", self.handle_redirect)
        state.num_401_calls = 1

        return r
