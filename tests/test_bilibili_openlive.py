"""Public-behavior tests for the Open Live session models and WS bootstrap.

Coverage: deterministic request-body serialization, strict response-envelope
parsing, start/heartbeat/end response-model validation (required fields and
rejection of malformed or unexpected responses), and the WebSocket
auth-body framing that integrates with the existing wire codec.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili import (  # noqa: E402
    EndRequest,
    EndResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    IdentityCode,
    MalformedResponseError,
    Operation,
    ProtocolVersion,
    Secret,
    StartRequest,
    StartResponse,
    WebSocketInfo,
    decode,
    decode_auth_reply,
    encode_auth_frame,
    encode_frame,
    parse_envelope,
)

IDENTITY = "synthetic-identity-code-0001"
APP_ID = 12345
GAME_ID = "game-0001"
AUTH_BODY = '{"uid":0,"roomid":1,"key":"ws-token-0001"}'
WSS_LINK = "wss://example.test/sub"


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class RequestModelTests(unittest.TestCase):
    def test_start_request_body_is_exact_and_ordered(self):
        request = StartRequest(code=IdentityCode(IDENTITY), app_id=APP_ID)
        self.assertEqual(
            request.body_json(),
            f'{{"code":"{IDENTITY}","app_id":{APP_ID}}}',
        )

    def test_start_request_redacts_identity_code(self):
        request = StartRequest(code=IdentityCode(IDENTITY), app_id=APP_ID)
        self.assertNotIn(IDENTITY, repr(request))

    def test_heartbeat_request_body_is_exact(self):
        request = HeartbeatRequest(game_id=GAME_ID)
        self.assertEqual(request.body_json(), f'{{"game_id":"{GAME_ID}"}}')

    def test_end_request_body_is_exact_and_ordered(self):
        request = EndRequest(app_id=APP_ID, game_id=GAME_ID)
        self.assertEqual(
            request.body_json(), f'{{"app_id":{APP_ID},"game_id":"{GAME_ID}"}}'
        )

    def test_invalid_app_id_rejected(self):
        for bad in (0, -1, True, 1.5, "123"):
            with self.subTest(app_id=bad):
                with self.assertRaises(ValueError):
                    StartRequest(code=IdentityCode(IDENTITY), app_id=bad)

    def test_invalid_game_id_rejected(self):
        for bad in ("", "bad\u0000id", None, 123):
            with self.subTest(game_id=bad):
                with self.assertRaises(ValueError):
                    HeartbeatRequest(game_id=bad)


class ParseEnvelopeTests(unittest.TestCase):
    def test_valid_success_envelope(self):
        envelope = parse_envelope(
            _compact({"code": 0, "message": "success", "data": {}}).encode("utf-8")
        )
        self.assertEqual(envelope.code, 0)
        self.assertEqual(envelope.message, "success")
        self.assertEqual(envelope.data, {})

    def test_valid_error_envelope(self):
        envelope = parse_envelope(
            _compact({"code": 1000, "message": "bad code", "data": None}).encode(
                "utf-8"
            )
        )
        self.assertEqual(envelope.code, 1000)
        self.assertIsNone(envelope.data)

    def test_non_utf8_body_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(b"\xff\xfe\xfd")

    def test_invalid_json_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(b"{not json")

    def test_non_object_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(b"[1,2,3]")

    def test_missing_key_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(_compact({"code": 0, "message": "ok"}).encode("utf-8"))

    def test_unknown_key_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(
                _compact({"code": 0, "message": "ok", "data": {}, "extra": 1}).encode(
                    "utf-8"
                )
            )

    def test_non_integer_code_rejected(self):
        for bad in (True, 1.5, "0"):
            with self.subTest(code=bad):
                with self.assertRaises(MalformedResponseError):
                    parse_envelope(
                        _compact({"code": bad, "message": "ok", "data": {}}).encode(
                            "utf-8"
                        )
                    )

    def test_non_string_message_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(
                _compact({"code": 0, "message": 1, "data": {}}).encode("utf-8")
            )

    def test_success_requires_object_data(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(
                _compact({"code": 0, "message": "ok", "data": None}).encode("utf-8")
            )

    def test_non_finite_json_rejected(self):
        with self.assertRaises(MalformedResponseError):
            parse_envelope(b'{"code":0,"message":"ok","data":{"x":NaN}}')

    def test_envelope_repr_redacts_data(self):
        auth_body = '{"uid":0,"roomid":1,"key":"ws-token-0001"}'
        envelope = parse_envelope(
            _compact(
                {
                    "code": 0,
                    "message": "success",
                    "data": {"websocket_info": {"auth_body": auth_body}},
                }
            ).encode("utf-8")
        )
        self.assertNotIn(auth_body, repr(envelope))
        self.assertNotIn("ws-token-0001", repr(envelope))
        self.assertIn("<redacted>", repr(envelope))


class StartResponseTests(unittest.TestCase):
    def _data(self, **overrides):
        data = {
            "game_info": {"game_id": GAME_ID},
            "websocket_info": {
                "wss_link": [WSS_LINK],
                "auth_body": AUTH_BODY,
            },
        }
        data.update(overrides)
        return data

    def test_valid_start_response(self):
        start = StartResponse.from_data(self._data())
        self.assertEqual(start.game_id, GAME_ID)
        self.assertEqual(start.websocket_info.wss_link, (WSS_LINK,))
        self.assertEqual(start.websocket_info.auth_body.value, AUTH_BODY)

    def test_websocket_info_redacts_auth_body(self):
        start = StartResponse.from_data(self._data())
        self.assertNotIn(AUTH_BODY, repr(start.websocket_info))
        self.assertNotIn("ws-token-0001", repr(start))

    def test_missing_game_info_rejected(self):
        data = self._data()
        del data["game_info"]
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_missing_game_id_rejected(self):
        data = self._data()
        del data["game_info"]["game_id"]
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_unknown_game_info_key_rejected(self):
        data = self._data()
        data["game_info"]["extra"] = "x"
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_missing_websocket_info_rejected(self):
        data = self._data()
        del data["websocket_info"]
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_empty_wss_link_rejected(self):
        data = self._data()
        data["websocket_info"]["wss_link"] = []
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_non_ws_wss_link_rejected(self):
        data = self._data()
        data["websocket_info"]["wss_link"] = ["http://example.test/sub"]
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_malformed_auth_body_rejected(self):
        data = self._data()
        data["websocket_info"]["auth_body"] = "not json"
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)

    def test_non_object_auth_body_rejected(self):
        data = self._data()
        data["websocket_info"]["auth_body"] = "[1,2,3]"
        with self.assertRaises(MalformedResponseError):
            StartResponse.from_data(data)


class HeartbeatAndEndResponseTests(unittest.TestCase):
    def test_heartbeat_without_interval(self):
        response = HeartbeatResponse.from_data({})
        self.assertIsNone(response.interval)

    def test_heartbeat_with_interval(self):
        response = HeartbeatResponse.from_data({"interval": 30})
        self.assertEqual(response.interval, 30)

    def test_heartbeat_invalid_interval_rejected(self):
        for bad in (0, -1, True, 1.5):
            with self.subTest(interval=bad):
                with self.assertRaises(MalformedResponseError):
                    HeartbeatResponse.from_data({"interval": bad})

    def test_heartbeat_unknown_key_rejected(self):
        with self.assertRaises(MalformedResponseError):
            HeartbeatResponse.from_data({"extra": 1})

    def test_end_response_accepts_empty_data(self):
        self.assertIsInstance(EndResponse.from_data({}), EndResponse)


class AuthFramingTests(unittest.TestCase):
    def test_encode_auth_frame_produces_a_decodable_auth_frame(self):
        frame = encode_auth_frame(AUTH_BODY, sequence=7)
        frames = decode(frame)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].operation, Operation.AUTH)
        self.assertEqual(frames[0].protocol_version, ProtocolVersion.PLAIN)
        self.assertEqual(frames[0].body, AUTH_BODY.encode("utf-8"))

    def test_encode_auth_frame_requires_json_object(self):
        with self.assertRaises(MalformedResponseError):
            encode_auth_frame("not json")

    def test_decode_auth_reply_success(self):
        frame = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.AUTH_REPLY,
            sequence=1,
            body=b'{"code":0}',
        )
        self.assertEqual(decode_auth_reply(frame), 0)

    def test_decode_auth_reply_non_zero_code(self):
        frame = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.AUTH_REPLY,
            sequence=1,
            body=b'{"code":-1}',
        )
        self.assertEqual(decode_auth_reply(frame), -1)

    def test_decode_auth_reply_rejects_wrong_operation(self):
        frame = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.AUTH,
            sequence=1,
            body=b'{"code":0}',
        )
        with self.assertRaises(MalformedResponseError):
            decode_auth_reply(frame)

    def test_decode_auth_reply_rejects_malformed_body(self):
        frame = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.AUTH_REPLY,
            sequence=1,
            body=b"not json",
        )
        with self.assertRaises(MalformedResponseError):
            decode_auth_reply(frame)


if __name__ == "__main__":
    unittest.main()
