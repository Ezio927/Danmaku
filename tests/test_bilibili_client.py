"""Public-behavior tests for the deterministic Open Live client core.

Coverage: full start/heartbeat/end lifecycle against in-memory HTTP/WebSocket
fakes (no external calls), deterministic signed requests, failure classification
onto the reused ConnectionLifecycle (credential/identity/auth/malformed-platform
fail closed; network/platform retryable), secret redaction in diagnostics, clean
shutdown clearing session data, and the two new fail-closed failure classes.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili import (  # noqa: E402
    AppCredentials,
    ConnectionState,
    FailureClass,
    HttpResponse,
    IdentityCodeError,
    IllegalTransitionError,
    OpenLiveClient,
    Operation,
    ProtocolVersion,
    Secret,
    SessionError,
    SessionStateError,
    TransportError,
    encode_auth_frame,
    encode_frame,
)

ACCESS_KEY_ID = "synthetic-access-key-0001"
ACCESS_KEY_SECRET = "synthetic-access-key-secret-0001"
APP_ID = 12345
GAME_ID = "game-0001"
IDENTITY = "synthetic-identity-code-0001"
AUTH_BODY = '{"uid":0,"roomid":1,"key":"ws-token-0001"}'
WSS_LINK = "wss://example.test/sub"
TIMESTAMP = 1735689600
NONCE = "synthetic-nonce-0001"

STATE = ConnectionState
FAILURE = FailureClass


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ok(payload):
    return HttpResponse(200, _compact(payload).encode("utf-8"))


def auth_reply(code=0):
    return encode_frame(
        protocol_version=ProtocolVersion.PLAIN,
        operation=Operation.AUTH_REPLY,
        sequence=1,
        body=_compact({"code": code}).encode("utf-8"),
    )


def start_payload(game_id=GAME_ID, auth_body=AUTH_BODY, wss=WSS_LINK):
    return {
        "code": 0,
        "message": "success",
        "data": {
            "game_info": {"game_id": game_id},
            "websocket_info": {"wss_link": [wss], "auth_body": auth_body},
        },
    }


class FakeHttp:
    def __init__(self, responder):
        self._responder = responder
        self.requests = []

    def request(self, method, url, headers, body):
        record = (method, url, dict(headers), body)
        self.requests.append(record)
        return self._responder(*record)


class FakeWebSocket:
    def __init__(self, replies=()):
        self.connected_url = None
        self.sent = []
        self._replies = list(replies)
        self.closed = False

    def connect(self, url):
        self.connected_url = url

    def send(self, data):
        self.sent.append(data)

    def receive(self):
        return self._replies.pop(0)

    def close(self):
        self.closed = True


def make_client(responder, ws_replies=(auth_reply(0),), base_url="https://example.test"):
    http = FakeHttp(responder)
    ws = FakeWebSocket(ws_replies)
    client = OpenLiveClient(
        credentials=AppCredentials(ACCESS_KEY_ID, Secret(ACCESS_KEY_SECRET)),
        app_id=APP_ID,
        http=http,
        websocket=ws,
        clock=lambda: TIMESTAMP,
        nonce_source=lambda: NONCE,
        base_url=base_url,
    )
    return client, http, ws


def session_responder():
    def respond(method, url, headers, body):
        if url.endswith("/v2/app/start"):
            return ok(start_payload())
        if url.endswith("/v2/app/heartbeat"):
            return ok({"code": 0, "message": "ok", "data": {"interval": 30}})
        if url.endswith("/v2/app/end"):
            return ok({"code": 0, "message": "ok", "data": {}})
        raise AssertionError(f"unexpected request url {url}")

    return respond


class HappyPathTests(unittest.TestCase):
    def test_full_session_lifecycle(self):
        client, http, ws = make_client(session_responder())
        self.assertEqual(client.state, STATE.UNCONFIGURED)

        client.configure()
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)

        start = client.start(IDENTITY)
        self.assertEqual(client.state, STATE.CONNECTED)
        self.assertEqual(start.game_id, GAME_ID)
        self.assertEqual(client.game_id, GAME_ID)
        self.assertEqual(client.websocket_info.wss_link, (WSS_LINK,))
        self.assertEqual(ws.connected_url, WSS_LINK)

        heartbeat = client.heartbeat()
        self.assertEqual(heartbeat.interval, 30)

        client.end()
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)
        self.assertIsNone(client.game_id)
        self.assertIsNone(client.websocket_info)
        self.assertTrue(ws.closed)

        # start -> heartbeat -> end produced exactly three signed requests.
        paths = [req[1] for req in http.requests]
        self.assertEqual(
            paths,
            [
                "https://example.test/v2/app/start",
                "https://example.test/v2/app/heartbeat",
                "https://example.test/v2/app/end",
            ],
        )

    def test_signed_request_is_deterministic_and_well_formed(self):
        client, http, _ = make_client(session_responder())
        client.configure()
        client.start(IDENTITY)

        method, url, headers, body = http.requests[0]
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://example.test/v2/app/start")
        self.assertEqual(headers["x-bili-signature-method"], "HMAC-SHA256")
        self.assertEqual(headers["x-bili-signature-nonce"], NONCE)
        self.assertEqual(headers["x-bili-timestamp"], str(TIMESTAMP))
        self.assertTrue(headers["Authorization"].startswith(f"{ACCESS_KEY_ID}:"))
        self.assertEqual(
            json.loads(body.decode("utf-8")),
            {"code": IDENTITY, "app_id": APP_ID},
        )

    def test_start_sends_the_auth_frame(self):
        client, _, ws = make_client(session_responder())
        client.configure()
        client.start(IDENTITY)
        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(ws.sent[0], encode_auth_frame(AUTH_BODY, sequence=1))


class FailureClassificationTests(unittest.TestCase):
    def _assert_failure(self, responder, ws_replies, expected_class, expected_state):
        client, http, ws = make_client(responder, ws_replies)
        client.configure()
        with self.assertRaises(SessionError) as ctx:
            client.start(IDENTITY)
        self.assertEqual(ctx.exception.failure_class, expected_class)
        self.assertEqual(client.state, expected_state)
        self.assertEqual(ctx.exception.failure_class, expected_class)
        return client, http, ws

    def test_credential_failure_is_fail_closed(self):
        self._assert_failure(
            lambda *_: HttpResponse(401, _compact(
                {"code": 1100, "message": "bad credential", "data": None}
            ).encode("utf-8")),
            (),
            FAILURE.CREDENTIAL,
            STATE.UNCONFIGURED,
        )

    def test_credential_business_code_is_fail_closed(self):
        self._assert_failure(
            lambda *_: ok({"code": 1100, "message": "bad credential", "data": None}),
            (),
            FAILURE.CREDENTIAL,
            STATE.UNCONFIGURED,
        )

    def test_identity_failure_is_fail_closed(self):
        self._assert_failure(
            lambda *_: ok({"code": 1000, "message": "bad identity", "data": None}),
            (),
            FAILURE.IDENTITY_CODE,
            STATE.WAITING_IDENTITY,
        )

    def test_not_live_failure_is_fail_closed(self):
        self._assert_failure(
            lambda *_: ok({"code": 1200, "message": "not live", "data": None}),
            (),
            FAILURE.NOT_LIVE,
            STATE.WAITING_IDENTITY,
        )

    def test_platform_failure_is_retryable(self):
        client, _, _ = self._assert_failure(
            lambda *_: ok({"code": 9999, "message": "boom", "data": None}),
            (),
            FAILURE.PLATFORM,
            STATE.RECONNECTING,
        )
        self.assertEqual(client.lifecycle.attempt, 1)
        self.assertGreater(client.lifecycle.next_delay_milliseconds, 0)

    def test_network_failure_is_retryable(self):
        def raise_network(*_):
            raise OSError("connection refused")

        client, _, _ = self._assert_failure(
            raise_network, (), FAILURE.NETWORK, STATE.RECONNECTING
        )
        self.assertEqual(client.lifecycle.attempt, 1)

    def test_transport_error_maps_to_network(self):
        def raise_transport(*_):
            raise TransportError("timeout")

        self._assert_failure(
            raise_transport, (), FAILURE.NETWORK, STATE.RECONNECTING
        )

    def test_malformed_response_is_fail_closed(self):
        self._assert_failure(
            lambda *_: HttpResponse(200, b"not json"),
            (),
            FAILURE.MALFORMED_PLATFORM,
            STATE.WAITING_IDENTITY,
        )

    def test_malformed_start_data_is_fail_closed(self):
        bad = start_payload()
        del bad["data"]["websocket_info"]
        self._assert_failure(
            lambda *_: ok(bad),
            (),
            FAILURE.MALFORMED_PLATFORM,
            STATE.WAITING_IDENTITY,
        )

    def test_auth_failure_is_fail_closed(self):
        client, http, ws = self._assert_failure(
            session_responder(),
            (auth_reply(-1),),
            FAILURE.AUTH,
            STATE.WAITING_IDENTITY,
        )
        self.assertEqual(client.lifecycle.attempt, 0)
        self.assertIsNone(client.game_id)
        self.assertTrue(ws.closed)

    def test_websocket_network_failure_is_retryable(self):
        class FlakyWs:
            def connect(self, url):
                raise OSError("socket closed")

            def send(self, data):  # pragma: no cover - not reached
                pass

            def receive(self):  # pragma: no cover - not reached
                return b""

            def close(self):
                pass

        http = FakeHttp(session_responder())
        client = OpenLiveClient(
            credentials=AppCredentials(ACCESS_KEY_ID, Secret(ACCESS_KEY_SECRET)),
            app_id=APP_ID,
            http=http,
            websocket=FlakyWs(),
            clock=lambda: TIMESTAMP,
            nonce_source=lambda: NONCE,
            base_url="https://example.test",
        )
        client.configure()
        with self.assertRaises(SessionError) as ctx:
            client.start(IDENTITY)
        self.assertEqual(ctx.exception.failure_class, FAILURE.NETWORK)
        self.assertEqual(client.state, STATE.RECONNECTING)
        self.assertEqual(client.lifecycle.attempt, 1)


class StateGuardTests(unittest.TestCase):
    def test_start_requires_configure(self):
        client, _, _ = make_client(session_responder())
        with self.assertRaises(IllegalTransitionError):
            client.start(IDENTITY)
        self.assertEqual(client.state, STATE.UNCONFIGURED)

    def test_malformed_identity_code_rejected_locally(self):
        client, _, _ = make_client(session_responder())
        client.configure()
        with self.assertRaises(IdentityCodeError):
            client.start("")
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)

    def test_heartbeat_requires_live_session(self):
        client, _, _ = make_client(session_responder())
        client.configure()
        with self.assertRaises(SessionStateError):
            client.heartbeat()

    def test_end_requires_live_session(self):
        client, _, _ = make_client(session_responder())
        client.configure()
        with self.assertRaises(SessionStateError):
            client.end()


class RedactionTests(unittest.TestCase):
    def test_last_request_summary_redacts_secrets(self):
        client, _, _ = make_client(session_responder())
        client.configure()
        client.start(IDENTITY)
        summary = client.last_request_summary
        self.assertIsNotNone(summary)
        self.assertNotIn(IDENTITY, summary)
        self.assertNotIn(ACCESS_KEY_SECRET, summary)

    def test_failure_message_redacts_secrets(self):
        client, _, _ = make_client(
            lambda *_: HttpResponse(401, _compact(
                {"code": 1100, "message": "bad credential", "data": None}
            ).encode("utf-8"))
        )
        client.configure()
        with self.assertRaises(SessionError) as ctx:
            client.start(IDENTITY)
        self.assertNotIn(IDENTITY, str(ctx.exception))
        self.assertNotIn(ACCESS_KEY_SECRET, str(ctx.exception))

    def test_client_state_never_exposes_identity_code(self):
        client, _, _ = make_client(session_responder())
        client.configure()
        client.start(IDENTITY)
        self.assertNotIn(IDENTITY, repr(client.__dict__))
        self.assertNotIn(IDENTITY, str(vars(client)))


class ShutdownTests(unittest.TestCase):
    def test_end_clears_all_session_data(self):
        client, http, ws = make_client(session_responder())
        client.configure()
        client.start(IDENTITY)
        client.end()
        self.assertIsNone(client.game_id)
        self.assertIsNone(client.websocket_info)
        self.assertIsNone(client.last_request_summary)
        self.assertTrue(ws.closed)
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)

    def test_close_is_idempotent(self):
        client, http, ws = make_client(session_responder())
        client.configure()
        client.start(IDENTITY)
        client.close()
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)
        self.assertIsNone(client.game_id)
        client.close()  # second close is a no-op
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)

    def test_close_best_effort_when_end_fails(self):
        def end_fails(method, url, headers, body):
            if url.endswith("/v2/app/end"):
                raise OSError("end failed")
            if url.endswith("/v2/app/start"):
                return ok(start_payload())
            raise AssertionError(f"unexpected url {url}")

        client, http, ws = make_client(end_fails)
        client.configure()
        client.start(IDENTITY)
        client.close()
        self.assertIsNone(client.game_id)
        self.assertIsNone(client.websocket_info)
        self.assertTrue(ws.closed)
        self.assertEqual(client.state, STATE.WAITING_IDENTITY)


class LifecycleExtensionTests(unittest.TestCase):
    def test_new_failure_class_strings(self):
        self.assertEqual(FAILURE.AUTH, "auth")
        self.assertEqual(FAILURE.MALFORMED_PLATFORM, "malformed-platform")

    def test_auth_and_malformed_platform_are_fail_closed(self):
        from danmaku.bilibili.lifecycle import ConnectionLifecycle

        for failure in (FAILURE.AUTH, FAILURE.MALFORMED_PLATFORM):
            for state in (STATE.STARTING_SESSION, STATE.CONNECTING, STATE.CONNECTED):
                with self.subTest(failure=failure, state=state):
                    machine = ConnectionLifecycle()
                    machine.configure()
                    machine.submit_identity_code(IDENTITY)
                    if state is STATE.CONNECTING:
                        machine.start_session()
                    elif state is STATE.CONNECTED:
                        machine.start_session()
                        machine.connected()
                    machine.report_failure(failure)
                    self.assertEqual(machine.state, STATE.WAITING_IDENTITY)
                    self.assertEqual(machine.attempt, 0)


if __name__ == "__main__":
    unittest.main()
