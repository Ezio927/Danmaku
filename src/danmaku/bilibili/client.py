"""Deterministic, offline-testable Open Live client core.

``OpenLiveClient`` composes the existing :class:`~danmaku.bilibili.lifecycle.ConnectionLifecycle`
state machine with the signed request construction
(:mod:`~danmaku.bilibili.signing`), the strict session response models, and the
WebSocket bootstrap/auth framing (:mod:`~danmaku.bilibili.openlive`). It models
the start -> heartbeat -> end session lifecycle against injected HTTP and
WebSocket seams, so every behaviour is reachable with deterministic in-memory
fakes and no network access.

Failures are classified onto the lifecycle's seven :class:`FailureClass` values.
Credential, identity-code, auth, and malformed-platform failures are fail-closed
(never blindly retried), while network and transient platform failures are
retryable with the existing bounded backoff. All secrets redact themselves in
diagnostic output, and clean shutdown clears every piece of session data.

Behaviour is documented in ``docs/bilibili-open-live-client-core.md``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from .lifecycle import ConnectionLifecycle, ConnectionState, FailureClass
from .openlive import (
    EndRequest,
    EndResponse,
    Envelope,
    HeartbeatRequest,
    HeartbeatResponse,
    MalformedResponseError,
    OpenLiveError,
    StartRequest,
    StartResponse,
    WebSocketInfo,
    decode_auth_reply,
    encode_auth_frame,
    parse_envelope,
)
from .signing import AppCredentials, IdentityCode, sign_request

__all__ = [
    "DEFAULT_BASE_URL",
    "END_PATH",
    "HEARTBEAT_PATH",
    "HttpResponse",
    "HttpTransport",
    "OpenLiveClient",
    "START_PATH",
    "SessionError",
    "SessionStateError",
    "TransportError",
    "WebSocketTransport",
]

DEFAULT_BASE_URL = "https://live-open.biliapi.com"
START_PATH = "/v2/app/start"
HEARTBEAT_PATH = "/v2/app/heartbeat"
END_PATH = "/v2/app/end"

# Documented Open Live business codes (see docs/bilibili-open-live-client-core.md).
_CODE_IDENTITY_INVALID = 1000
_CODE_IDENTITY_EXPIRED = 1001
_CODE_CREDENTIAL_INVALID = 1100
_CODE_CREDENTIAL_FORBIDDEN = 1101
_CODE_NOT_LIVE = 1200

_IDENTITY_CODES = frozenset({_CODE_IDENTITY_INVALID, _CODE_IDENTITY_EXPIRED})
_CREDENTIAL_CODES = frozenset({_CODE_CREDENTIAL_INVALID, _CODE_CREDENTIAL_FORBIDDEN})
_NOT_LIVE_CODES = frozenset({_CODE_NOT_LIVE})

_FAIL_CLOSED = frozenset(
    {
        FailureClass.CREDENTIAL,
        FailureClass.IDENTITY_CODE,
        FailureClass.NOT_LIVE,
        FailureClass.AUTH,
        FailureClass.MALFORMED_PLATFORM,
    }
)

_ACTIVE_STATES = frozenset(
    {
        ConnectionState.STARTING_SESSION,
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
        ConnectionState.RECONNECTING,
    }
)


class SessionError(OpenLiveError):
    """A classified Open Live session failure.

    ``failure_class`` records which :class:`FailureClass` the failure maps onto,
    and ``detail`` is a fixed, secret-safe description (it never carries the
    identity code, access key secret, or auth body).
    """

    def __init__(self, failure_class: FailureClass, detail: str) -> None:
        self.failure_class = failure_class
        super().__init__(f"{failure_class.value}: {detail}")


class SessionStateError(OpenLiveError):
    """Raised when an operation is not legal for the current session state."""


class TransportError(OpenLiveError):
    """A transport-level (network) failure; retryable via bounded backoff."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """The raw HTTP response returned by an :class:`HttpTransport`."""

    status: int
    body: bytes


class HttpTransport(Protocol):
    """Minimal injectable HTTP seam for one signed POST request."""

    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> HttpResponse: ...


class WebSocketTransport(Protocol):
    """Minimal injectable WebSocket seam for the bootstrap/auth handshake."""

    def connect(self, url: str) -> None: ...

    def send(self, data: bytes) -> None: ...

    def receive(self) -> bytes: ...

    def close(self) -> None: ...


def _classify_http_failure(code: int) -> FailureClass:
    """Map a business code onto a :class:`FailureClass`.

    HTTP 401/403 is handled before parsing (always credential); this function
    classifies a parsed envelope's non-zero business code.
    """
    if code in _IDENTITY_CODES:
        return FailureClass.IDENTITY_CODE
    if code in _CREDENTIAL_CODES:
        return FailureClass.CREDENTIAL
    if code in _NOT_LIVE_CODES:
        return FailureClass.NOT_LIVE
    return FailureClass.PLATFORM


class OpenLiveClient:
    """Deterministic, offline-testable Open Live session client.

    The client owns a :class:`ConnectionLifecycle` and drives it through the
    start/heartbeat/end flow. HTTP and WebSocket traffic flows only through the
    injected :class:`HttpTransport` and :class:`WebSocketTransport` seams; the
    signing timestamp and nonce are supplied by injectable ``clock`` and
    ``nonce_source`` callables so tests stay fully deterministic.
    """

    def __init__(
        self,
        *,
        credentials: AppCredentials,
        app_id: int,
        http: HttpTransport,
        websocket: WebSocketTransport,
        lifecycle: ConnectionLifecycle | None = None,
        clock: Callable[[], int] | None = None,
        nonce_source: Callable[[], str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if not isinstance(credentials, AppCredentials):
            raise TypeError("credentials must be AppCredentials")
        if (
            isinstance(app_id, bool)
            or not isinstance(app_id, int)
            or app_id < 1
        ):
            raise ValueError("app_id must be a positive integer")
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url must be a non-empty string")

        self._credentials = credentials
        self._app_id = app_id
        self._http = http
        self._websocket = websocket
        self._lifecycle = lifecycle if lifecycle is not None else ConnectionLifecycle()
        self._clock = clock if clock is not None else lambda: int(time.time())
        self._nonce_source = (
            nonce_source if nonce_source is not None else lambda: uuid.uuid4().hex
        )
        self._base_url = base_url.rstrip("/")

        self._game_id: str | None = None
        self._websocket_info = None
        self._ws_connected = False
        self._last_summary: str | None = None

    # -- read-only state ----------------------------------------------------

    @property
    def lifecycle(self) -> ConnectionLifecycle:
        """The composed connection lifecycle state machine."""
        return self._lifecycle

    @property
    def state(self) -> ConnectionState:
        """The current lifecycle state."""
        return self._lifecycle.state

    @property
    def game_id(self) -> str | None:
        """The active session game id, or ``None`` when no session is live."""
        return self._game_id

    @property
    def websocket_info(self) -> WebSocketInfo | None:
        """The active WebSocket bootstrap info, or ``None`` when no session is live."""
        return self._websocket_info

    @property
    def last_request_summary(self) -> str | None:
        """A secret-safe summary of the most recent signed request."""
        return self._last_summary

    # -- lifecycle controls -------------------------------------------------

    def configure(self) -> None:
        """Move ``unconfigured`` -> ``waiting for identity code``."""
        self._lifecycle.configure()

    def start(self, identity_code: str) -> StartResponse:
        """Start a session from ``waiting for identity code``.

        Validates the identity code, drives the lifecycle through ``starting
        session`` and ``connecting``, signs and sends the Start request, then
        performs the WebSocket bootstrap/auth handshake and lands in
        ``connected``. The identity code is never stored after this call.
        """
        code = IdentityCode(identity_code)
        self._lifecycle.submit_identity_code(code.value)
        self._lifecycle.start_session()

        request = StartRequest(code=code, app_id=self._app_id)
        envelope = self._post_success(START_PATH, request.body_json())
        try:
            start = StartResponse.from_data(envelope.data)
        except MalformedResponseError as exc:
            self._raise_failure(FailureClass.MALFORMED_PLATFORM, str(exc))

        self._game_id = start.game_id
        self._websocket_info = start.websocket_info

        try:
            self._websocket.connect(start.websocket_info.wss_link[0])
            self._ws_connected = True
            self._websocket.send(encode_auth_frame(start.websocket_info.auth_body.value))
            reply = self._websocket.receive()
        except (TransportError, OSError) as exc:
            self._raise_failure(
                FailureClass.NETWORK, f"websocket failure: {type(exc).__name__}"
            )
        try:
            auth_code = decode_auth_reply(reply)
        except MalformedResponseError as exc:
            self._raise_failure(FailureClass.MALFORMED_PLATFORM, str(exc))
        if auth_code != 0:
            self._raise_failure(FailureClass.AUTH, f"auth rejected (code {auth_code})")

        self._lifecycle.connected()
        return start

    def heartbeat(self) -> HeartbeatResponse:
        """Send one signed heartbeat for the live session."""
        self._require_connected()
        request = HeartbeatRequest(game_id=self._game_id)
        envelope = self._post_success(HEARTBEAT_PATH, request.body_json())
        try:
            return HeartbeatResponse.from_data(envelope.data)
        except MalformedResponseError as exc:
            self._raise_failure(FailureClass.MALFORMED_PLATFORM, str(exc))

    def end(self) -> EndResponse:
        """End the live session through the End endpoint and tear down."""
        self._require_connected()
        request = EndRequest(app_id=self._app_id, game_id=self._game_id)
        envelope = self._post_success(END_PATH, request.body_json())
        try:
            result = EndResponse.from_data(envelope.data)
        except MalformedResponseError as exc:
            self._raise_failure(FailureClass.MALFORMED_PLATFORM, str(exc))
        self._teardown()
        return result

    def close(self) -> None:
        """Best-effort clean shutdown.

        Ends an active session through the End endpoint when possible, then
        always closes the socket, clears all session data, and returns the
        lifecycle to a non-active state. Idempotent.
        """
        if self._lifecycle.state is ConnectionState.CONNECTED:
            try:
                self.end()
            except OpenLiveError:
                pass
        self._teardown()

    # -- internals ----------------------------------------------------------

    def _require_connected(self) -> None:
        if self._lifecycle.state is not ConnectionState.CONNECTED:
            raise SessionStateError(
                "operation requires a live session (connected state)"
            )

    def _teardown(self) -> None:
        """Idempotently close the socket and clear all session data."""
        if self._ws_connected:
            self._websocket.close()
            self._ws_connected = False
        self._game_id = None
        self._websocket_info = None
        self._last_summary = None
        if self._lifecycle.state in _ACTIVE_STATES:
            self._lifecycle.stop()

    def _send(self, path: str, body: str) -> HttpResponse:
        """Sign and send one signed POST, mapping transport errors to NETWORK."""
        signed = sign_request(
            credentials=self._credentials,
            method="POST",
            path=path,
            body=body,
            timestamp=self._clock(),
            nonce=self._nonce_source(),
        )
        self._last_summary = signed.summary()
        try:
            return self._http.request(
                "POST", self._base_url + path, signed.headers, body.encode("utf-8")
            )
        except (TransportError, OSError) as exc:
            self._raise_failure(
                FailureClass.NETWORK, f"network failure: {type(exc).__name__}"
            )

    def _post_success(self, path: str, body: str) -> Envelope:
        """Send a request and validate a success envelope, classifying failures."""
        response = self._send(path, body)
        if response.status in (401, 403):
            self._raise_failure(
                FailureClass.CREDENTIAL, f"{path} rejected (http {response.status})"
            )
        try:
            envelope = parse_envelope(response.body)
        except MalformedResponseError as exc:
            self._raise_failure(FailureClass.MALFORMED_PLATFORM, str(exc))
        if response.status == 200 and envelope.code == 0:
            return envelope
        failure = _classify_http_failure(envelope.code)
        self._raise_failure(
            failure, f"{path} rejected (http {response.status}, code {envelope.code})"
        )

    def _raise_failure(self, failure: FailureClass, detail: str) -> None:
        """Report a classified failure and tear down when it is fail-closed."""
        self._lifecycle.report_failure(failure)
        if failure in _FAIL_CLOSED:
            self._teardown()
        raise SessionError(failure, detail)
