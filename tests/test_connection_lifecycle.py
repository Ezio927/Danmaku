"""Deterministic connection lifecycle state machine tests.

Coverage: canonical states, legal transitions, illegal transitions (rejected
without state changes), failure classification, retry/stop behaviour, bounded
exponential backoff, attempt reset, explicit controls, identity-code
validation, and backoff-policy validation.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili.lifecycle import (  # noqa: E402
    DEFAULT_BASE_DELAY_MILLISECONDS,
    DEFAULT_MAX_DELAY_MILLISECONDS,
    BackoffPolicy,
    ConnectionLifecycle,
    ConnectionState,
    FailureClass,
    IllegalTransitionError,
    InvalidIdentityCodeError,
)

STATE = ConnectionState
FAILURE = FailureClass

VALID_CODE = "identity-code-123"


def in_state(state: ConnectionState) -> ConnectionLifecycle:
    """Build a machine deterministically parked in ``state``."""
    machine = ConnectionLifecycle()
    if state is STATE.UNCONFIGURED:
        return machine
    machine.configure()
    if state is STATE.WAITING_IDENTITY:
        return machine
    machine.submit_identity_code(VALID_CODE)
    if state is STATE.STARTING_SESSION:
        return machine
    machine.start_session()
    if state is STATE.CONNECTING:
        return machine
    machine.connected()
    if state is STATE.CONNECTED:
        return machine
    machine.reconnect()
    if state is STATE.RECONNECTING:
        return machine
    raise AssertionError(f"unhandled state {state!r}")


class CanonicalStateTests(unittest.TestCase):
    def test_canonical_state_strings(self):
        self.assertEqual(STATE.UNCONFIGURED, "unconfigured")
        self.assertEqual(STATE.WAITING_IDENTITY, "waiting for identity code")
        self.assertEqual(STATE.STARTING_SESSION, "starting session")
        self.assertEqual(STATE.CONNECTING, "connecting")
        self.assertEqual(STATE.CONNECTED, "connected")
        self.assertEqual(STATE.RECONNECTING, "reconnecting")

    def test_str_of_state_is_canonical_value(self):
        self.assertEqual(str(STATE.CONNECTED), "connected")

    def test_initial_state_is_unconfigured(self):
        self.assertEqual(ConnectionLifecycle().state, STATE.UNCONFIGURED)

    def test_canonical_failure_class_strings(self):
        self.assertEqual(FAILURE.CREDENTIAL, "credential")
        self.assertEqual(FAILURE.IDENTITY_CODE, "identity-code")
        self.assertEqual(FAILURE.NOT_LIVE, "not-live")
        self.assertEqual(FAILURE.NETWORK, "network")
        self.assertEqual(FAILURE.PLATFORM, "platform")


class HappyPathTransitionTests(unittest.TestCase):
    def test_linear_flow_to_connected(self):
        machine = ConnectionLifecycle()
        machine.configure()
        self.assertEqual(machine.state, STATE.WAITING_IDENTITY)
        machine.submit_identity_code(VALID_CODE)
        self.assertEqual(machine.state, STATE.STARTING_SESSION)
        machine.start_session()
        self.assertEqual(machine.state, STATE.CONNECTING)
        machine.connected()
        self.assertEqual(machine.state, STATE.CONNECTED)


class ReconnectTests(unittest.TestCase):
    def test_explicit_reconnect_enters_reconnecting(self):
        machine = in_state(STATE.CONNECTED)
        machine.reconnect()
        self.assertEqual(machine.state, STATE.RECONNECTING)

    def test_reconnect_schedules_base_delay_and_first_attempt(self):
        machine = in_state(STATE.CONNECTED)
        machine.reconnect()
        self.assertEqual(machine.attempt, 1)
        self.assertEqual(
            machine.next_delay_milliseconds, DEFAULT_BASE_DELAY_MILLISECONDS
        )

    def test_connect_attempts_again_then_connected_resets(self):
        machine = in_state(STATE.CONNECTED)
        machine.reconnect()
        machine.connect()
        self.assertEqual(machine.state, STATE.CONNECTING)
        self.assertEqual(machine.attempt, 1)
        machine.connected()
        self.assertEqual(machine.state, STATE.CONNECTED)
        self.assertEqual(machine.attempt, 0)


class ExplicitStopTests(unittest.TestCase):
    def test_stop_from_connected_returns_to_waiting_identity(self):
        machine = in_state(STATE.CONNECTED)
        machine.stop()
        self.assertEqual(machine.state, STATE.WAITING_IDENTITY)
        self.assertEqual(machine.attempt, 0)

    def test_stop_from_connecting(self):
        machine = in_state(STATE.CONNECTING)
        machine.stop()
        self.assertEqual(machine.state, STATE.WAITING_IDENTITY)

    def test_stop_from_starting_session(self):
        machine = in_state(STATE.STARTING_SESSION)
        machine.stop()
        self.assertEqual(machine.state, STATE.WAITING_IDENTITY)

    def test_stop_from_reconnecting(self):
        machine = in_state(STATE.RECONNECTING)
        machine.stop()
        self.assertEqual(machine.state, STATE.WAITING_IDENTITY)
        self.assertEqual(machine.attempt, 0)

    def test_after_stop_a_new_session_requires_a_new_identity_code(self):
        machine = in_state(STATE.CONNECTED)
        machine.stop()
        machine.submit_identity_code("another-code")
        self.assertEqual(machine.state, STATE.STARTING_SESSION)


class FailureClassificationTests(unittest.TestCase):
    def test_credential_failure_stops_to_unconfigured(self):
        for state in (STATE.STARTING_SESSION, STATE.CONNECTING, STATE.CONNECTED):
            with self.subTest(state=state):
                machine = in_state(state)
                machine.report_failure(FAILURE.CREDENTIAL)
                self.assertEqual(machine.state, STATE.UNCONFIGURED)
                self.assertEqual(machine.attempt, 0)

    def test_credential_failure_from_reconnecting_stops(self):
        machine = in_state(STATE.RECONNECTING)
        machine.report_failure(FAILURE.CREDENTIAL)
        self.assertEqual(machine.state, STATE.UNCONFIGURED)
        self.assertEqual(machine.attempt, 0)

    def test_identity_code_failure_stops_to_waiting_identity(self):
        for state in (STATE.STARTING_SESSION, STATE.CONNECTING, STATE.CONNECTED):
            with self.subTest(state=state):
                machine = in_state(state)
                machine.report_failure(FAILURE.IDENTITY_CODE)
                self.assertEqual(machine.state, STATE.WAITING_IDENTITY)
                self.assertEqual(machine.attempt, 0)

    def test_not_live_failure_stops_to_waiting_identity(self):
        for state in (STATE.STARTING_SESSION, STATE.CONNECTING, STATE.CONNECTED):
            with self.subTest(state=state):
                machine = in_state(state)
                machine.report_failure(FAILURE.NOT_LIVE)
                self.assertEqual(machine.state, STATE.WAITING_IDENTITY)
                self.assertEqual(machine.attempt, 0)

    def test_network_failure_retries_from_connected(self):
        machine = in_state(STATE.CONNECTED)
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.state, STATE.RECONNECTING)
        self.assertEqual(machine.attempt, 1)

    def test_network_failure_retries_from_connecting(self):
        machine = in_state(STATE.CONNECTING)
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.state, STATE.RECONNECTING)
        self.assertEqual(machine.attempt, 1)

    def test_platform_failure_retries_like_network(self):
        machine = in_state(STATE.CONNECTED)
        machine.report_failure(FAILURE.PLATFORM)
        self.assertEqual(machine.state, STATE.RECONNECTING)
        self.assertEqual(machine.attempt, 1)

    def test_retryable_failures_are_distinct_classes(self):
        self.assertNotEqual(FAILURE.NETWORK, FAILURE.PLATFORM)
        self.assertNotEqual(FAILURE.CREDENTIAL, FAILURE.IDENTITY_CODE)
        self.assertNotEqual(FAILURE.NOT_LIVE, FAILURE.NETWORK)

    def test_last_failure_tracks_reported_class(self):
        machine = in_state(STATE.CONNECTED)
        self.assertIsNone(machine.last_failure)
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.last_failure, FAILURE.NETWORK)
        machine.connect()
        machine.connected()
        machine.report_failure(FAILURE.NOT_LIVE)
        self.assertEqual(machine.last_failure, FAILURE.NOT_LIVE)

    def test_report_failure_requires_a_failure_class(self):
        machine = in_state(STATE.CONNECTED)
        for bad in ("network", None, 1, True):
            with self.subTest(value=bad):
                with self.assertRaises(TypeError):
                    machine.report_failure(bad)
                self.assertEqual(machine.state, STATE.CONNECTED)


class BackoffTests(unittest.TestCase):
    def test_default_exponential_sequence(self):
        policy = BackoffPolicy()
        self.assertEqual(
            [policy.delay_for(n) for n in range(1, 8)],
            [500, 1000, 2000, 4000, 5000, 5000, 5000],
        )

    def test_delay_is_bounded_at_max_for_large_attempts(self):
        policy = BackoffPolicy()
        for attempt in (10, 100, 100_000):
            with self.subTest(attempt=attempt):
                self.assertEqual(policy.delay_for(attempt), DEFAULT_MAX_DELAY_MILLISECONDS)

    def test_injectable_policy_values(self):
        policy = BackoffPolicy(
            base_delay_milliseconds=100,
            max_delay_milliseconds=1000,
            multiplier=3,
        )
        self.assertEqual(
            [policy.delay_for(n) for n in range(1, 7)],
            [100, 300, 900, 1000, 1000, 1000],
        )

    def test_machine_honors_injected_policy(self):
        machine = ConnectionLifecycle(
            base_delay_milliseconds=100,
            max_delay_milliseconds=1000,
            multiplier=3,
        )
        machine.configure()
        machine.submit_identity_code(VALID_CODE)
        machine.start_session()
        machine.connected()
        machine.report_failure(FAILURE.NETWORK)  # attempt 1
        self.assertEqual(machine.next_delay_milliseconds, 100)
        machine.connect()
        machine.report_failure(FAILURE.NETWORK)  # attempt 2
        self.assertEqual(machine.next_delay_milliseconds, 300)
        machine.connect()
        machine.report_failure(FAILURE.NETWORK)  # attempt 3
        self.assertEqual(machine.next_delay_milliseconds, 900)
        machine.connect()
        machine.report_failure(FAILURE.NETWORK)  # attempt 4 -> capped
        self.assertEqual(machine.next_delay_milliseconds, 1000)

    def test_delay_is_zero_outside_reconnecting(self):
        machine = in_state(STATE.CONNECTED)
        self.assertEqual(machine.next_delay_milliseconds, 0)
        machine.reconnect()
        self.assertGreater(machine.next_delay_milliseconds, 0)
        machine.connect()
        self.assertEqual(machine.next_delay_milliseconds, 0)


class AttemptResetTests(unittest.TestCase):
    def test_connected_resets_attempt_after_failures(self):
        machine = in_state(STATE.CONNECTED)
        machine.report_failure(FAILURE.NETWORK)  # attempt 1
        machine.connect()
        machine.report_failure(FAILURE.PLATFORM)  # attempt 2
        self.assertEqual(machine.attempt, 2)
        machine.connect()
        machine.connected()
        self.assertEqual(machine.attempt, 0)

    def test_attempt_restarts_at_base_after_reset(self):
        machine = in_state(STATE.CONNECTED)
        machine.report_failure(FAILURE.NETWORK)
        machine.connect()
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.attempt, 2)
        machine.connect()
        machine.connected()
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.attempt, 1)
        self.assertEqual(
            machine.next_delay_milliseconds, DEFAULT_BASE_DELAY_MILLISECONDS
        )

    def test_stop_resets_attempt(self):
        machine = in_state(STATE.CONNECTED)
        machine.reconnect()
        machine.connect()
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.attempt, 2)
        machine.stop()
        self.assertEqual(machine.attempt, 0)

    def test_hard_failure_resets_attempt(self):
        machine = in_state(STATE.CONNECTED)
        machine.reconnect()
        self.assertEqual(machine.attempt, 1)
        machine.report_failure(FAILURE.CREDENTIAL)
        self.assertEqual(machine.attempt, 0)


class IllegalTransitionTests(unittest.TestCase):
    def test_illegal_transition_rejected_without_state_change(self):
        legal_from = {
            "configure": {STATE.UNCONFIGURED},
            "submit_identity_code": {STATE.WAITING_IDENTITY},
            "start_session": {STATE.STARTING_SESSION},
            "connect": {STATE.RECONNECTING},
            "connected": {STATE.CONNECTING},
            "reconnect": {STATE.CONNECTED},
            "stop": {
                STATE.STARTING_SESSION,
                STATE.CONNECTING,
                STATE.CONNECTED,
                STATE.RECONNECTING,
            },
        }
        for event, legal in legal_from.items():
            for state in STATE:
                if state in legal:
                    continue
                with self.subTest(event=event, state=state):
                    machine = in_state(state)
                    before = machine.state
                    with self.assertRaises(IllegalTransitionError):
                        if event == "submit_identity_code":
                            machine.submit_identity_code(VALID_CODE)
                        else:
                            getattr(machine, event)()
                    self.assertEqual(machine.state, before)

    def test_each_event_is_illegal_from_unconfigured(self):
        machine = ConnectionLifecycle()
        for event in (
            "submit_identity_code",
            "start_session",
            "connect",
            "connected",
            "reconnect",
            "stop",
        ):
            with self.subTest(event=event):
                with self.assertRaises(IllegalTransitionError):
                    if event == "submit_identity_code":
                        machine.submit_identity_code(VALID_CODE)
                    else:
                        getattr(machine, event)()
                self.assertEqual(machine.state, STATE.UNCONFIGURED)

    def test_configure_only_legal_once(self):
        machine = in_state(STATE.WAITING_IDENTITY)
        with self.assertRaises(IllegalTransitionError):
            machine.configure()
        self.assertEqual(machine.state, STATE.WAITING_IDENTITY)

    def test_stop_illegal_from_unconfigured_and_waiting(self):
        for state in (STATE.UNCONFIGURED, STATE.WAITING_IDENTITY):
            with self.subTest(state=state):
                machine = in_state(state)
                with self.assertRaises(IllegalTransitionError):
                    machine.stop()
                self.assertEqual(machine.state, state)

    def test_reconnect_illegal_outside_connected(self):
        for state in STATE:
            if state is STATE.CONNECTED:
                continue
            with self.subTest(state=state):
                machine = in_state(state)
                with self.assertRaises(IllegalTransitionError):
                    machine.reconnect()
                self.assertEqual(machine.state, state)

    def test_connected_illegal_outside_connecting(self):
        for state in STATE:
            if state is STATE.CONNECTING:
                continue
            with self.subTest(state=state):
                machine = in_state(state)
                with self.assertRaises(IllegalTransitionError):
                    machine.connected()
                self.assertEqual(machine.state, state)

    def test_connect_illegal_outside_reconnecting(self):
        for state in STATE:
            if state is STATE.RECONNECTING:
                continue
            with self.subTest(state=state):
                machine = in_state(state)
                with self.assertRaises(IllegalTransitionError):
                    machine.connect()
                self.assertEqual(machine.state, state)

    def test_retryable_failure_illegal_when_not_attempting(self):
        for state in (
            STATE.UNCONFIGURED,
            STATE.WAITING_IDENTITY,
            STATE.STARTING_SESSION,
            STATE.RECONNECTING,
        ):
            with self.subTest(state=state):
                machine = in_state(state)
                with self.assertRaises(IllegalTransitionError):
                    machine.report_failure(FAILURE.NETWORK)
                self.assertEqual(machine.state, state)
                self.assertEqual(machine.attempt, 0 if state is not STATE.RECONNECTING else 1)

    def test_hard_failure_illegal_from_unconfigured_and_waiting(self):
        for state in (STATE.UNCONFIGURED, STATE.WAITING_IDENTITY):
            for failure in (FAILURE.CREDENTIAL, FAILURE.IDENTITY_CODE, FAILURE.NOT_LIVE):
                with self.subTest(state=state, failure=failure):
                    machine = in_state(state)
                    with self.assertRaises(IllegalTransitionError):
                        machine.report_failure(failure)
                    self.assertEqual(machine.state, state)

    def test_illegal_transition_preserves_attempt(self):
        machine = in_state(STATE.RECONNECTING)
        machine.connect()
        machine.report_failure(FAILURE.NETWORK)
        self.assertEqual(machine.attempt, 2)
        with self.assertRaises(IllegalTransitionError):
            machine.connected()  # connected() only legal from connecting
        self.assertEqual(machine.attempt, 2)
        self.assertEqual(machine.state, STATE.RECONNECTING)


class IdentityCodeValidationTests(unittest.TestCase):
    def test_valid_identity_code_accepted(self):
        machine = in_state(STATE.WAITING_IDENTITY)
        machine.submit_identity_code("a" * 256)
        self.assertEqual(machine.state, STATE.STARTING_SESSION)

    def test_invalid_identity_codes_rejected_without_state_change(self):
        bad_codes = ["", "a" * 257, "bad\u0000code", None, 123, True]
        for code in bad_codes:
            with self.subTest(code=code):
                machine = in_state(STATE.WAITING_IDENTITY)
                with self.assertRaises(InvalidIdentityCodeError):
                    machine.submit_identity_code(code)
                self.assertEqual(machine.state, STATE.WAITING_IDENTITY)

    def test_identity_code_is_never_stored(self):
        machine = ConnectionLifecycle()
        machine.configure()
        machine.submit_identity_code("sensitive-code-value")
        self.assertNotIn("sensitive-code-value", str(vars(machine)))
        self.assertNotIn("sensitive-code-value", repr(machine.__dict__))

    def test_invalid_code_in_wrong_state_reports_illegal_transition(self):
        machine = in_state(STATE.CONNECTED)
        with self.assertRaises(IllegalTransitionError):
            machine.submit_identity_code("")
        self.assertEqual(machine.state, STATE.CONNECTED)


class PolicyValidationTests(unittest.TestCase):
    def test_defaults(self):
        policy = BackoffPolicy()
        self.assertEqual(policy.base_delay_milliseconds, 500)
        self.assertEqual(policy.max_delay_milliseconds, 5000)
        self.assertEqual(policy.multiplier, 2)

    def test_default_constants_match_policy(self):
        self.assertEqual(DEFAULT_BASE_DELAY_MILLISECONDS, 500)
        self.assertEqual(DEFAULT_MAX_DELAY_MILLISECONDS, 5000)

    def test_non_integer_values_rejected(self):
        for field, bad in (
            ("base_delay_milliseconds", "500"),
            ("base_delay_milliseconds", True),
            ("max_delay_milliseconds", 5000.0),
            ("multiplier", "2"),
        ):
            with self.subTest(field=field, value=bad):
                with self.assertRaises(TypeError):
                    BackoffPolicy(**{field: bad})

    def test_out_of_bounds_values_rejected(self):
        with self.assertRaises(ValueError):
            BackoffPolicy(base_delay_milliseconds=0)
        with self.assertRaises(ValueError):
            BackoffPolicy(base_delay_milliseconds=-1)
        with self.assertRaises(ValueError):
            BackoffPolicy(base_delay_milliseconds=6000, max_delay_milliseconds=5000)
        with self.assertRaises(ValueError):
            BackoffPolicy(multiplier=1)
        with self.assertRaises(ValueError):
            BackoffPolicy(multiplier=0)

    def test_policy_is_frozen(self):
        policy = BackoffPolicy()
        with self.assertRaises(AttributeError):
            policy.base_delay_milliseconds = 1000

    def test_delay_for_rejects_non_positive_attempt(self):
        policy = BackoffPolicy()
        for bad in (0, -1, True, 1.5, "1"):
            with self.subTest(attempt=bad):
                with self.assertRaises(ValueError):
                    policy.delay_for(bad)


class ConstructorTests(unittest.TestCase):
    def test_backoff_object_accepted(self):
        policy = BackoffPolicy(base_delay_milliseconds=123, max_delay_milliseconds=999)
        machine = ConnectionLifecycle(backoff=policy)
        self.assertIs(machine.backoff, policy)

    def test_individual_values_rejected_with_backoff_object(self):
        policy = BackoffPolicy()
        with self.assertRaises(TypeError):
            ConnectionLifecycle(backoff=policy, base_delay_milliseconds=100)

    def test_backoff_must_be_a_policy(self):
        with self.assertRaises(TypeError):
            ConnectionLifecycle(backoff="not-a-policy")


if __name__ == "__main__":
    unittest.main()
