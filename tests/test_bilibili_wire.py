"""Public-behavior tests for the offline Bilibili WebSocket wire codec.

Coverage: committed non-sensitive fixtures (valid event, heartbeat, and
heartbeat-reply frames), deterministic decode of plain and zlib message bodies,
explicit framing/header validation (truncated, malformed, unsupported, and
oversized packets), heartbeat handling without manufactured product messages,
unknown-operation classification, and reuse of the canonical recorded-event
adapter boundary.
"""

import json
import struct
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili import (  # noqa: E402
    BilibiliAdapter,
    BilibiliAdapterError,
    BilibiliWireCodec,
    ControlFrame,
    FrameHeader,
    HEADER_LENGTH,
    MAX_PACKET_LENGTH,
    MalformedFrameError,
    MessageEnvelope,
    Operation,
    OversizedPacketError,
    PayloadDecodeError,
    ProtocolVersion,
    TruncatedPacketError,
    UnsupportedOperationError,
    UnsupportedProtocolVersionError,
    decode,
    encode_frame,
    encode_heartbeat,
    encode_heartbeat_reply,
    encode_message_plain,
    encode_message_zlib,
    parse_header,
)

FIXTURES = ROOT / "docs" / "bilibili-wire-fixtures"
ENVELOPES = ROOT / "docs" / "bilibili-fixtures"


def _load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _frame_bytes(name):
    return bytes.fromhex(_load_fixture(name)["frameHex"])


def _load_envelope(name):
    return json.loads((ENVELOPES / name).read_text(encoding="utf-8"))


def _compact(envelope):
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def _header(packet_length, version, operation, sequence, header_length=HEADER_LENGTH):
    return struct.pack(
        ">IHHII", packet_length, header_length, version, operation, sequence
    )


class FixtureDecodeTests(unittest.TestCase):
    def test_heartbeat_fixture_decodes_to_control_frame(self):
        frames = decode(_frame_bytes("heartbeat.json"))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertIsInstance(frame, ControlFrame)
        self.assertEqual(frame.operation, Operation.HEARTBEAT)
        self.assertEqual(frame.protocol_version, ProtocolVersion.HEARTBEAT)
        self.assertEqual(frame.body, b"")

    def test_heartbeat_reply_fixture_decodes_to_control_frame(self):
        frames = decode(_frame_bytes("heartbeat-reply.json"))
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertIsInstance(frame, ControlFrame)
        self.assertEqual(frame.operation, Operation.HEARTBEAT_REPLY)
        self.assertEqual(frame.body, struct.pack(">I", 12345))

    def test_plain_danmaku_fixture_extracts_the_recorded_envelope(self):
        frames = decode(_frame_bytes("message-plain-danmaku.json"))
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], MessageEnvelope)
        self.assertEqual(
            json.loads(frames[0].text), _load_envelope("danmaku.json")
        )

    def test_zlib_danmaku_fixture_extracts_the_recorded_envelope(self):
        frames = decode(_frame_bytes("message-zlib-danmaku.json"))
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], MessageEnvelope)
        self.assertEqual(
            json.loads(frames[0].text), _load_envelope("danmaku.json")
        )

    def test_zlib_multi_fixture_extracts_every_envelope_in_order(self):
        frames = decode(_frame_bytes("message-zlib-multi.json"))
        self.assertEqual(len(frames), 2)
        self.assertEqual(
            [json.loads(frames[0].text), json.loads(frames[1].text)],
            [_load_envelope("danmaku.json"), _load_envelope("gift.json")],
        )

    def test_every_committed_fixture_is_valid_json(self):
        for path in sorted(FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_malformed_truncated_fixture_rejected(self):
        with self.assertRaises(TruncatedPacketError):
            decode(_frame_bytes("malformed-truncated.json"))

    def test_unsupported_operation_fixture_rejected(self):
        with self.assertRaises(UnsupportedOperationError):
            decode(_frame_bytes("unsupported-operation.json"))


class ParseHeaderTests(unittest.TestCase):
    def test_parse_header_returns_declared_fields(self):
        data = encode_heartbeat(sequence=7)
        header = parse_header(data)
        self.assertEqual(
            header,
            FrameHeader(
                packet_length=16,
                header_length=16,
                protocol_version=1,
                operation=2,
                sequence=7,
            ),
        )

    def test_parse_header_rejects_truncated_header(self):
        with self.assertRaises(TruncatedPacketError):
            parse_header(b"\x00" * 15)

    def test_parse_header_rejects_bad_header_length(self):
        data = _header(16, 1, 2, 1, header_length=15)
        with self.assertRaises(MalformedFrameError):
            parse_header(data)

    def test_parse_header_rejects_packet_length_below_header(self):
        data = _header(15, 1, 2, 1)
        with self.assertRaises(MalformedFrameError):
            parse_header(data)

    def test_parse_header_rejects_unknown_operation(self):
        data = _header(16, 1, 99, 1)
        with self.assertRaises(UnsupportedOperationError):
            parse_header(data)

    def test_parse_header_rejects_unsupported_version(self):
        data = _header(16, 99, 2, 1)
        with self.assertRaises(UnsupportedProtocolVersionError):
            parse_header(data)

    def test_parse_header_rejects_non_bytes(self):
        with self.assertRaises(TypeError):
            parse_header("not bytes")


class FramingValidationTests(unittest.TestCase):
    def test_truncated_body_rejected(self):
        data = _header(100, 1, 2, 1)
        with self.assertRaises(TruncatedPacketError):
            decode(data)

    def test_declared_oversize_rejected(self):
        data = _header(MAX_PACKET_LENGTH + 1, 1, 2, 1)
        with self.assertRaises(OversizedPacketError):
            decode(data)

    def test_actual_oversize_rejected(self):
        data = _header(MAX_PACKET_LENGTH, 1, 2, 1) + b"\x00" * (
            MAX_PACKET_LENGTH - HEADER_LENGTH + 1
        )
        with self.assertRaises(OversizedPacketError):
            decode(data)

    def test_exactly_max_size_frame_accepted_at_boundary(self):
        data = _header(MAX_PACKET_LENGTH, 1, 2, 1) + b"\x00" * (
            MAX_PACKET_LENGTH - HEADER_LENGTH
        )
        frames = decode(data)
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], ControlFrame)

    def test_trailing_bytes_rejected(self):
        data = encode_heartbeat() + b"\x00"
        with self.assertRaises(MalformedFrameError):
            decode(data)

    def test_empty_input_rejected(self):
        with self.assertRaises(TruncatedPacketError):
            decode(b"")

    def test_non_bytes_rejected(self):
        with self.assertRaises(TypeError):
            decode("not bytes")


class ControlPacketTests(unittest.TestCase):
    def test_heartbeat_produces_no_message(self):
        frames = decode(encode_heartbeat(sequence=3))
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], ControlFrame)
        self.assertNotIsInstance(frames[0], MessageEnvelope)

    def test_heartbeat_reply_produces_no_message(self):
        frames = decode(encode_heartbeat_reply(sequence=3, popularity=99))
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], ControlFrame)
        self.assertEqual(frames[0].body, struct.pack(">I", 99))

    def test_auth_and_auth_reply_are_classified_without_messages(self):
        for operation in (Operation.AUTH, Operation.AUTH_REPLY):
            with self.subTest(operation=operation):
                data = encode_frame(
                    protocol_version=ProtocolVersion.PLAIN,
                    operation=operation,
                    sequence=1,
                    body=b"{}",
                )
                frames = decode(data)
                self.assertEqual(len(frames), 1)
                self.assertIsInstance(frames[0], ControlFrame)
                self.assertEqual(frames[0].operation, operation)

    def test_unknown_operation_classified_explicitly(self):
        data = _header(16, 1, 99, 1)
        with self.assertRaises(UnsupportedOperationError):
            decode(data)


class MessageDecodeTests(unittest.TestCase):
    def test_plain_message_round_trips_envelope(self):
        envelope = _load_envelope("danmaku.json")
        text = _compact(envelope)
        frames = decode(encode_message_plain(text, sequence=5))
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], MessageEnvelope)
        self.assertEqual(frames[0].sequence, 5)
        self.assertEqual(json.loads(frames[0].text), envelope)

    def test_zlib_message_round_trips_envelope(self):
        envelope = _load_envelope("super-chat.json")
        text = _compact(envelope)
        frames = decode(encode_message_zlib(text, sequence=9))
        self.assertEqual(len(frames), 1)
        self.assertEqual(json.loads(frames[0].text), envelope)

    def test_zlib_message_round_trips_multiple_envelopes(self):
        texts = [_compact(_load_envelope(n)) for n in ("gift.json", "guard.json")]
        frames = decode(encode_message_zlib(texts, sequence=2))
        self.assertEqual(
            [json.loads(f.text) for f in frames],
            [_load_envelope("gift.json"), _load_envelope("guard.json")],
        )

    def test_zlib_decompression_matches_stdlib(self):
        text = _compact(_load_envelope("danmaku.json"))
        inner = encode_message_plain(text, sequence=1)
        expected = zlib.compress(inner)
        actual = encode_message_zlib(text, sequence=1)
        self.assertEqual(actual[HEADER_LENGTH:], expected)


class PayloadRejectionTests(unittest.TestCase):
    def test_brotli_message_rejected(self):
        data = encode_frame(
            protocol_version=ProtocolVersion.BROTLI,
            operation=Operation.MESSAGE,
            sequence=1,
            body=b"\x00\x01",
        )
        with self.assertRaises(UnsupportedProtocolVersionError):
            decode(data)

    def test_message_with_heartbeat_version_rejected(self):
        data = encode_frame(
            protocol_version=ProtocolVersion.HEARTBEAT,
            operation=Operation.MESSAGE,
            sequence=1,
            body=b"",
        )
        with self.assertRaises(MalformedFrameError):
            decode(data)

    def test_invalid_utf8_body_rejected(self):
        data = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.MESSAGE,
            sequence=1,
            body=b"\xff\xfe\xfd",
        )
        with self.assertRaises(MalformedFrameError):
            decode(data)

    def test_invalid_json_body_rejected(self):
        data = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.MESSAGE,
            sequence=1,
            body=b"{not json",
        )
        with self.assertRaises(MalformedFrameError):
            decode(data)

    def test_non_object_json_body_rejected(self):
        for body in (b"[]", b"123", b'"text"'):
            with self.subTest(body=body):
                data = encode_frame(
                    protocol_version=ProtocolVersion.PLAIN,
                    operation=Operation.MESSAGE,
                    sequence=1,
                    body=body,
                )
                with self.assertRaises(MalformedFrameError):
                    decode(data)

    def test_non_finite_json_rejected(self):
        bad = encode_frame(
            protocol_version=ProtocolVersion.PLAIN,
            operation=Operation.MESSAGE,
            sequence=1,
            body=b'{"x":NaN}',
        )
        with self.assertRaises(MalformedFrameError):
            decode(bad)

    def test_invalid_zlib_body_rejected(self):
        data = encode_frame(
            protocol_version=ProtocolVersion.ZLIB,
            operation=Operation.MESSAGE,
            sequence=1,
            body=b"not zlib data",
        )
        with self.assertRaises(PayloadDecodeError):
            decode(data)

    def test_empty_zlib_payload_rejected(self):
        data = encode_frame(
            protocol_version=ProtocolVersion.ZLIB,
            operation=Operation.MESSAGE,
            sequence=1,
            body=zlib.compress(b""),
        )
        with self.assertRaises(MalformedFrameError):
            decode(data)

    def test_truncated_inner_packet_rejected(self):
        inner = _header(20, ProtocolVersion.PLAIN, Operation.MESSAGE, 1)
        data = encode_frame(
            protocol_version=ProtocolVersion.ZLIB,
            operation=Operation.MESSAGE,
            sequence=1,
            body=zlib.compress(inner),
        )
        with self.assertRaises(TruncatedPacketError):
            decode(data)


class AdapterIntegrationTests(unittest.TestCase):
    def test_decode_messages_normalizes_plain_fixture(self):
        codec = BilibiliWireCodec(adapter=BilibiliAdapter(start_sequence=1))
        messages = codec.decode_messages(_frame_bytes("message-plain-danmaku.json"))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].id, "bilibili:d-100001")
        self.assertEqual(messages[0].kind, "danmaku")
        self.assertEqual(dict(messages[0].data), {"text": "主播好！"})

    def test_decode_messages_normalizes_multi_fixture_in_order(self):
        codec = BilibiliWireCodec(adapter=BilibiliAdapter(start_sequence=1))
        messages = codec.decode_messages(_frame_bytes("message-zlib-multi.json"))
        self.assertEqual([m.sequence for m in messages], [1, 2])
        self.assertEqual(
            [m.id for m in messages],
            ["bilibili:d-100001", "bilibili:g-200001"],
        )
        self.assertEqual(dict(messages[1].data)["totalAmountMilliCny"], 1000)

    def test_heartbeat_produces_no_product_messages(self):
        codec = BilibiliWireCodec(adapter=BilibiliAdapter(start_sequence=1))
        self.assertEqual(codec.decode_messages(encode_heartbeat()), [])
        self.assertEqual(codec.decode_messages(encode_heartbeat_reply()), [])
        self.assertEqual(codec.adapter.next_sequence, 1)

    def test_unknown_command_envelope_rejected_without_coercion(self):
        codec = BilibiliWireCodec(adapter=BilibiliAdapter(start_sequence=1))
        text = '{"cmd":"COMBO_SEND","eventId":"x-1","timestamp":1735689600000,'
        text += '"user":{"uid":"1","uname":"A"},"data":{"text":"hi"}}'
        frame = encode_message_plain(text)
        with self.assertRaises(BilibiliAdapterError):
            codec.decode_messages(frame)
        self.assertEqual(codec.adapter.next_sequence, 1)


if __name__ == "__main__":
    unittest.main()
