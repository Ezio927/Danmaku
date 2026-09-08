"""Credential-free, transport-independent Bilibili connection lifecycle.

This module defines the deterministic connection state machine that owns the
canonical ``unconfigured`` -> ``waiting for identity code`` -> ``starting
session`` -> ``connecting`` -> ``connected`` lifecycle plus the bounded
``reconnecting`` retry state. It is deliberately offline and side-effect free:
it performs no network access, stores no credentials or identity code, and
makes no persistence or transport decision. It only decides which state is
legal next, classifies failures, and schedules bounded exponential reconnect
delays for a future transport to consume.

Behaviour is documented in ``docs/bilibili-connection-lifecycle.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "BackoffPolicy",
    "ConnectionLifecycle",
    "ConnectionLifecycleError",
    "ConnectionState",
    "DEFAULT_BASE_DELAY_MILLISECONDS",
    "DEFAULT_MAX_DELAY_MILLISECONDS",
    "DEFAULT_MULTIPLIER",
    "FailureClass",
    "IllegalTransitionError",
    "InvalidIdentityCodeError",
]

DEFAULT_BASE_DELAY_MILLISECONDS = 500
DEFAULT_MAX_DELAY_MILLISECONDS = 5000
DEFAULT_MULTIPLIER = 2

MAX_IDENTITY_CODE_LENGTH = 256


class ConnectionState(StrEnum):
    """The six canonical Bilibili connection states."""

    UNCONFIGURED = "unconfigured"
    WAITING_IDENTITY = "waiting for identity code"
    STARTING_SESSION = "starting session"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class FailureClass(StrEnum):
    """The five distinct Bilibili connection failure classes.

    ``credential``, ``identity-code``, and ``not-live`` are fail-closed stop
    failures (no retry). ``network`` and ``platform`` are transient and
    retryable with bounded exponential backoff.
    """

    CREDENTIAL = "credential"
    IDENTITY_CODE = "identity-code"
    NOT_LIVE = "not-live"
    NETWORK = "network"
    PLATFORM = "platform"


class ConnectionLifecycleError(Exception):
    """Base class for connection lifecycle errors."""


class IllegalTransitionError(ConnectionLifecycleError):
    """Raised when an event is not legal in the current state."""


class InvalidIdentityCodeError(ConnectionLifecycleError):
    """Raised when a submitted identity code is malformed."""


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Immutable bounded exponential backoff schedule.

    ``delay_for(attempt)`` returns ``base * multiplier ** (attempt - 1)``
    capped at ``max_delay_milliseconds``. The cap makes every delay bounded and
    deterministic regardless of how large ``attempt`` grows.
    """

    base_delay_milliseconds: int = DEFAULT_BASE_DELAY_MILLISECONDS
    max_delay_milliseconds: int = DEFAULT_MAX_DELAY_MILLISECONDS
    multiplier: int = DEFAULT_MULTIPLIER

    def __post_init__(self) -> None:
        for field in (
            "base_delay_milliseconds",
            "max_delay_milliseconds",
            "multiplier",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field} must be an integer")
        if self.base_delay_milliseconds < 1:
            raise ValueError("base_delay_milliseconds must be >= 1")
        if self.max_delay_milliseconds < self.base_delay_milliseconds:
            raise ValueError(
                "max_delay_milliseconds must be >= base_delay_milliseconds"
            )
        if self.multiplier < 2:
            raise ValueError("multiplier must be >= 2")

    def delay_for(self, attempt: int) -> int:
        """Return the bounded delay in milliseconds for a 1-indexed attempt."""
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise ValueError("attempt must be a positive integer")
        delay = self.base_delay_milliseconds
        for _ in range(attempt - 1):
            if delay >= self.max_delay_milliseconds:
                break
            delay = min(delay * self.multiplier, self.max_delay_milliseconds)
        return delay


def _validate_identity_code(code: object) -> None:
    """Validate a transient identity code without storing it."""
    if not isinstance(code, str):
        raise InvalidIdentityCodeError("identity code must be a string")
    if not code:
        raise InvalidIdentityCodeError("identity code must not be empty")
    if len(code) > MAX_IDENTITY_CODE_LENGTH:
        raise InvalidIdentityCodeError(
            f"identity code must be at most {MAX_IDENTITY_CODE_LENGTH} characters"
        )
    if any(ord(char) < 0x20 for char in code):
        raise InvalidIdentityCodeError(
            "identity code must not contain control characters"
        )


_ACTIVE_STATES = frozenset(
    {
        ConnectionState.STARTING_SESSION,
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
        ConnectionState.RECONNECTING,
    }
)

_RETRYABLE_STATES = frozenset(
    {
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
    }
)


class ConnectionLifecycle:
    """Deterministic, credential-free Bilibili connection state machine.

    The machine exposes six canonical states and seven events. Each event is
    legal only in a documented subset of states; any other call raises
    :class:`IllegalTransitionError` without changing state or attempt
    accounting. A transient ``network``/``platform`` failure moves the machine
    into ``reconnecting`` and increments the attempt counter, which schedules
    the next bounded exponential delay; a ``connected`` transition resets the
    attempt counter. A ``credential``, ``identity-code``, or ``not-live``
    failure is fail-closed: it stops the machine (no retry) and returns to
    ``unconfigured`` or ``waiting for identity code`` as appropriate.

    Legal transitions (``->`` is the resulting state):

    * ``configure``: ``unconfigured`` -> ``waiting for identity code``;
    * ``submit_identity_code``: ``waiting for identity code`` -> ``starting
      session``;
    * ``start_session``: ``starting session`` -> ``connecting``;
    * ``connect``: ``reconnecting`` -> ``connecting``;
    * ``connected``: ``connecting`` -> ``connected``;
    * ``reconnect``: ``connected`` -> ``reconnecting``;
    * ``stop``: ``starting session``/``connecting``/``connected``/
      ``reconnecting`` -> ``waiting for identity code``;
    * ``report_failure(network|platform)``: ``connecting``/``connected`` ->
      ``reconnecting``;
    * ``report_failure(credential)``: ``starting session``/``connecting``/
      ``connected``/``reconnecting`` -> ``unconfigured``;
    * ``report_failure(identity-code|not-live)``: ``starting session``/
      ``connecting``/``connected``/``reconnecting`` -> ``waiting for identity
      code``.
    """

    def __init__(
        self,
        *,
        backoff: BackoffPolicy | None = None,
        base_delay_milliseconds: int | None = None,
        max_delay_milliseconds: int | None = None,
        multiplier: int | None = None,
    ) -> None:
        if backoff is not None:
            if not isinstance(backoff, BackoffPolicy):
                raise TypeError("backoff must be a BackoffPolicy")
            if any(
                value is not None
                for value in (
                    base_delay_milliseconds,
                    max_delay_milliseconds,
                    multiplier,
                )
            ):
                raise TypeError(
                    "provide either backoff or individual policy values, not both"
                )
            self._backoff = backoff
        else:
            self._backoff = BackoffPolicy(
                base_delay_milliseconds=(
                    DEFAULT_BASE_DELAY_MILLISECONDS
                    if base_delay_milliseconds is None
                    else base_delay_milliseconds
                ),
                max_delay_milliseconds=(
                    DEFAULT_MAX_DELAY_MILLISECONDS
                    if max_delay_milliseconds is None
                    else max_delay_milliseconds
                ),
                multiplier=DEFAULT_MULTIPLIER if multiplier is None else multiplier,
            )
        self._state = ConnectionState.UNCONFIGURED
        self._attempt = 0
        self._last_failure: FailureClass | None = None

    @property
    def state(self) -> ConnectionState:
        """The current canonical connection state."""
        return self._state

    @property
    def attempt(self) -> int:
        """The current reconnect attempt number (1-indexed).

        It is ``0`` when no reconnect cycle is active (``unconfigured``,
        ``waiting for identity code``, ``starting session``, the initial
        ``connecting``, or ``connected``) and ``1..N`` while reconnecting or
        retrying after a failure.
        """
        return self._attempt

    @property
    def next_delay_milliseconds(self) -> int:
        """The scheduled reconnect delay, or ``0`` outside ``reconnecting``."""
        if self._state is not ConnectionState.RECONNECTING or self._attempt < 1:
            return 0
        return self._backoff.delay_for(self._attempt)

    @property
    def backoff(self) -> BackoffPolicy:
        """The immutable backoff policy in use."""
        return self._backoff

    @property
    def last_failure(self) -> FailureClass | None:
        """The most recently reported failure class, or ``None``."""
        return self._last_failure

    def _require_state(self, event: str, allowed: frozenset[ConnectionState]) -> None:
        if self._state not in allowed:
            ordered = ", ".join(sorted(state.value for state in allowed))
            raise IllegalTransitionError(
                f"{event} is not legal in state {self._state.value!r}; "
                f"legal only from: {ordered}"
            )

    def _transition(
        self, event: str, allowed: frozenset[ConnectionState], target: ConnectionState
    ) -> None:
        self._require_state(event, allowed)
        self._state = target

    def configure(self) -> None:
        """Move ``unconfigured`` -> ``waiting for identity code``."""
        self._transition(
            "configure",
            frozenset({ConnectionState.UNCONFIGURED}),
            ConnectionState.WAITING_IDENTITY,
        )

    def submit_identity_code(self, code: object) -> None:
        """Validate ``code`` and move ``waiting for identity code`` -> ``starting session``.

        The identity code is validated but never stored. An invalid code raises
        :class:`InvalidIdentityCodeError`; a wrong state raises
        :class:`IllegalTransitionError`. Neither changes state.
        """
        self._require_state(
            "submit_identity_code", frozenset({ConnectionState.WAITING_IDENTITY})
        )
        _validate_identity_code(code)
        self._state = ConnectionState.STARTING_SESSION

    def start_session(self) -> None:
        """Move ``starting session`` -> ``connecting``."""
        self._transition(
            "start_session",
            frozenset({ConnectionState.STARTING_SESSION}),
            ConnectionState.CONNECTING,
        )

    def connect(self) -> None:
        """Attempt a connection: move ``reconnecting`` -> ``connecting``."""
        self._transition(
            "connect",
            frozenset({ConnectionState.RECONNECTING}),
            ConnectionState.CONNECTING,
        )

    def connected(self) -> None:
        """Record a successful connection and reset attempt accounting."""
        self._transition(
            "connected",
            frozenset({ConnectionState.CONNECTING}),
            ConnectionState.CONNECTED,
        )
        self._attempt = 0

    def reconnect(self) -> None:
        """Explicitly reconnect: move ``connected`` -> ``reconnecting``.

        The attempt counter increments and the next bounded exponential delay
        is scheduled.
        """
        self._transition(
            "reconnect",
            frozenset({ConnectionState.CONNECTED}),
            ConnectionState.RECONNECTING,
        )
        self._attempt += 1

    def stop(self) -> None:
        """Explicitly stop an active session -> ``waiting for identity code``."""
        self._transition(
            "stop", _ACTIVE_STATES, ConnectionState.WAITING_IDENTITY
        )
        self._attempt = 0

    def report_failure(self, failure_class: FailureClass) -> None:
        """Report a failure and apply its fail-closed retry/stop behaviour.

        ``network`` and ``platform`` are retryable: from ``connecting`` or
        ``connected`` they move to ``reconnecting`` and increment the attempt
        counter. ``credential``, ``identity-code``, and ``not-live`` are
        fail-closed stop failures: they move to ``unconfigured`` (credential)
        or ``waiting for identity code`` (identity-code, not-live) and reset
        the attempt counter.
        """
        if not isinstance(failure_class, FailureClass):
            raise TypeError("failure_class must be a FailureClass")
        if failure_class in (FailureClass.NETWORK, FailureClass.PLATFORM):
            self._transition(
                "report_failure", _RETRYABLE_STATES, ConnectionState.RECONNECTING
            )
            self._attempt += 1
        elif failure_class is FailureClass.CREDENTIAL:
            self._transition(
                "report_failure", _ACTIVE_STATES, ConnectionState.UNCONFIGURED
            )
            self._attempt = 0
        else:
            self._transition(
                "report_failure", _ACTIVE_STATES, ConnectionState.WAITING_IDENTITY
            )
            self._attempt = 0
        self._last_failure = failure_class
