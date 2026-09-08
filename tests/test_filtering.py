"""Server-side OBS delivery filtering tests.

Coverage: policy normalization, the deterministic suppression decision for each
independent predicate, the shared snapshot/live decision in the distribution
hub, and end-to-end suppression through the loopback ``aiohttp`` ``Service``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import socket
import sys
import unittest
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.core.hub import DistributionHub  # noqa: E402
from danmaku.core.model import Message  # noqa: E402
from danmaku.core.snapshot import SnapshotStore  # noqa: E402
from danmaku.server.config import ServiceConfig  # noqa: E402
from danmaku.server.filtering import (  # noqa: E402
    DEFAULT_GIFT_THRESHOLD_MILLI_CNY,
    FilteringPolicy,
    normalize_keywords,
    normalize_nicknames,
)
from danmaku.server.runner import Service  # noqa: E402

HELLO = '{"protocolVersion":1,"type":"hello","payload":{}}'


def _fixed_clock() -> int:
    """Deterministic clock that keeps every fixed 2026-01-01 fixture in-window.

    Returning the epoch places the snapshot-retention cutoff at epoch minus
    five minutes, so no fixture timestamp is ever trimmed. Tests that exercise
    the retention window itself inject their own clock instead.
    """
    return 0

_DEFAULT_DATA = {
    "danmaku": {"text": "hello world"},
    "gift": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
    "guard": {"tier": "captain", "months": 1},
    "superChat": {
        "text": "hello world",
        "amountMilliCny": 30000,
        "durationSeconds": 60,
    },
}


def make_message(
    sequence: int,
    kind: str = "danmaku",
    user_id: str = "user:alice",
    name: str = "Alice",
    data: dict | None = None,
) -> Message:
    return Message.from_dict(
        {
            "id": f"filter:{sequence:04d}",
            "sequence": sequence,
            "receivedAt": "2026-01-01T00:00:00.000Z",
            "source": "mock",
            "kind": kind,
            "user": {"id": user_id, "name": name},
            "data": data if data is not None else dict(_DEFAULT_DATA[kind]),
        }
    )


def gift(sequence: int, amount_milli_cny: int, user_id: str = "user:bob") -> Message:
    return make_message(
        sequence,
        kind="gift",
        user_id=user_id,
        name="Bob",
        data={
            "giftName": "Star",
            "quantity": 1,
            "totalAmountMilliCny": amount_milli_cny,
        },
    )


class NormalizationTests(unittest.TestCase):
    def test_nicknames_trim_casefold_deduplicate_and_drop_empty(self):
        result = normalize_nicknames(["Alice", "  ALICE  ", "", "bob", "Bob", "   "])
        self.assertEqual(result, frozenset({"alice", "bob"}))

    def test_nicknames_unicode_casefold(self):
        self.assertEqual(normalize_nicknames(["Straße", "STRASSE"]), frozenset({"strasse"}))

    def test_keywords_trim_casefold_deduplicate_and_drop_empty(self):
        result = normalize_keywords(["Hello", "  hello ", "", "WORLD", "world"])
        self.assertEqual(result, frozenset({"hello", "world"}))

    def test_keywords_unicode_casefold(self):
        # U+212A KELVIN SIGN casefolds to "k" (its lower() would stay unchanged).
        self.assertEqual(normalize_keywords(["K"]), frozenset({"k"}))

    def test_empty_input_normalizes_to_empty_set(self):
        self.assertEqual(normalize_nicknames([]), frozenset())
        self.assertEqual(normalize_keywords(()), frozenset())

    def test_non_string_entries_rejected(self):
        with self.assertRaises(TypeError):
            normalize_nicknames(["Alice", 123])
        with self.assertRaises(TypeError):
            normalize_keywords(["hello", None])


class FilteringPolicyConstructionTests(unittest.TestCase):
    def test_defaults(self):
        policy = FilteringPolicy()
        self.assertEqual(policy.deny_user_ids, frozenset())
        self.assertEqual(policy.deny_nicknames, frozenset())
        self.assertEqual(policy.keywords, frozenset())
        self.assertEqual(
            policy.gift_threshold_milli_cny, DEFAULT_GIFT_THRESHOLD_MILLI_CNY
        )

    def test_default_gift_threshold_is_exactly_100(self):
        self.assertEqual(DEFAULT_GIFT_THRESHOLD_MILLI_CNY, 100)

    def test_entries_normalized_on_construction(self):
        policy = FilteringPolicy(
            deny_user_ids=["user:alice", "user:alice"],
            deny_nicknames=[" Alice ", "ALICE", ""],
            keywords=["Hello", "hello"],
        )
        self.assertEqual(policy.deny_user_ids, frozenset({"user:alice"}))
        self.assertEqual(policy.deny_nicknames, frozenset({"alice"}))
        self.assertEqual(policy.keywords, frozenset({"hello"}))

    def test_user_ids_are_exact_not_casefolded(self):
        policy = FilteringPolicy(deny_user_ids=["User:Alice"])
        self.assertEqual(policy.deny_user_ids, frozenset({"User:Alice"}))

    def test_policy_is_frozen_and_holds_frozensets(self):
        policy = FilteringPolicy(deny_nicknames=["Alice"])
        self.assertIsInstance(policy.deny_user_ids, frozenset)
        self.assertIsInstance(policy.deny_nicknames, frozenset)
        self.assertIsInstance(policy.keywords, frozenset)
        with self.assertRaises(AttributeError):
            policy.gift_threshold_milli_cny = 200

    def test_threshold_must_be_a_non_negative_integer(self):
        FilteringPolicy(gift_threshold_milli_cny=0)
        with self.assertRaises(TypeError):
            FilteringPolicy(gift_threshold_milli_cny=True)
        with self.assertRaises(TypeError):
            FilteringPolicy(gift_threshold_milli_cny="100")
        with self.assertRaises(ValueError):
            FilteringPolicy(gift_threshold_milli_cny=-1)


class SuppressionTests(unittest.TestCase):
    def _policy(self, **kwargs) -> FilteringPolicy:
        return FilteringPolicy(**kwargs)

    def test_identity_user_id_exact_match(self):
        policy = self._policy(deny_user_ids={"user:alice"})
        self.assertTrue(policy.is_suppressed(make_message(1, user_id="user:alice")))
        self.assertFalse(policy.is_suppressed(make_message(2, user_id="user:bob")))
        # exact only: no prefix or partial match
        self.assertFalse(policy.is_suppressed(make_message(3, user_id="user:alice2")))

    def test_identity_user_id_is_case_sensitive(self):
        policy = self._policy(deny_user_ids={"User:Alice"})
        self.assertTrue(policy.is_suppressed(make_message(1, user_id="User:Alice")))
        self.assertFalse(policy.is_suppressed(make_message(2, user_id="user:alice")))

    def test_identity_nickname_normalized_exact(self):
        policy = self._policy(deny_nicknames=["  ALICE  "])
        self.assertTrue(policy.is_suppressed(make_message(1, name="Alice")))
        self.assertTrue(policy.is_suppressed(make_message(2, name="alice")))
        self.assertTrue(policy.is_suppressed(make_message(3, name=" Alice ")))
        # exact equality, never substring
        self.assertFalse(policy.is_suppressed(make_message(4, name="Alicia")))
        self.assertFalse(policy.is_suppressed(make_message(5, name="Ali")))

    def test_identity_nickname_unicode_casefold(self):
        policy = self._policy(deny_nicknames=["Straße"])
        self.assertTrue(policy.is_suppressed(make_message(1, name="STRASSE")))

    def test_keyword_matches_danmaku_text_case_insensitively(self):
        policy = self._policy(keywords=["  hello  "])
        self.assertTrue(
            policy.is_suppressed(
                make_message(1, kind="danmaku", data={"text": "HELLO world"})
            )
        )
        self.assertFalse(
            policy.is_suppressed(
                make_message(2, kind="danmaku", data={"text": "goodbye"})
            )
        )

    def test_keyword_matches_superchat_text(self):
        policy = self._policy(keywords=["secret"])
        self.assertTrue(
            policy.is_suppressed(
                make_message(
                    1,
                    kind="superChat",
                    data={
                        "text": "a SECRET!",
                        "amountMilliCny": 30000,
                        "durationSeconds": 60,
                    },
                )
            )
        )
        self.assertFalse(
            policy.is_suppressed(
                make_message(
                    2,
                    kind="superChat",
                    data={"text": "fine", "amountMilliCny": 30000, "durationSeconds": 60},
                )
            )
        )

    def test_keyword_is_substring_without_regex(self):
        policy = self._policy(keywords=["a.b"])
        self.assertTrue(
            policy.is_suppressed(
                make_message(1, kind="danmaku", data={"text": "xxa.bxx"})
            )
        )
        # a literal dot, not a regex wildcard
        self.assertFalse(
            policy.is_suppressed(
                make_message(2, kind="danmaku", data={"text": "xxaxbxx"})
            )
        )

    def test_keyword_never_matches_username(self):
        policy = self._policy(keywords=["alice"])
        self.assertFalse(
            policy.is_suppressed(
                make_message(
                    1,
                    kind="danmaku",
                    user_id="user:alice",
                    name="Alice",
                    data={"text": "hello"},
                )
            )
        )

    def test_keyword_never_matches_gift_name(self):
        policy = self._policy(keywords=["star"])
        self.assertFalse(
            policy.is_suppressed(
                make_message(1, kind="gift", user_id="user:star", name="Star")
            )
        )

    def test_keyword_never_matches_guard_tier_or_user(self):
        policy = self._policy(keywords=["captain", "dana"])
        self.assertFalse(
            policy.is_suppressed(make_message(1, kind="guard", name="Dana"))
        )

    def test_gift_threshold_default_filters_below_100(self):
        policy = self._policy()
        self.assertTrue(policy.is_suppressed(gift(1, 99)))
        self.assertFalse(policy.is_suppressed(gift(2, 100)))
        self.assertFalse(policy.is_suppressed(gift(3, 101)))

    def test_gift_threshold_adjusted(self):
        policy = self._policy(gift_threshold_milli_cny=500)
        self.assertTrue(policy.is_suppressed(gift(1, 499)))
        self.assertFalse(policy.is_suppressed(gift(2, 500)))

    def test_gift_threshold_never_applies_to_guard_or_superchat(self):
        policy = self._policy(gift_threshold_milli_cny=100)
        self.assertFalse(
            policy.is_suppressed(
                make_message(1, kind="guard", data={"tier": "captain", "months": 1})
            )
        )
        self.assertFalse(
            policy.is_suppressed(
                make_message(
                    2,
                    kind="superChat",
                    data={"text": "hi", "amountMilliCny": 5, "durationSeconds": 60},
                )
            )
        )

    def test_predicates_apply_independently(self):
        policy = self._policy(
            deny_user_ids={"user:alice"},
            keywords=["secret"],
            gift_threshold_milli_cny=100,
        )
        # identity deny reaches guard events (no text, no gift amount)
        self.assertTrue(
            policy.is_suppressed(
                make_message(1, kind="guard", user_id="user:alice", name="Dana")
            )
        )
        # keyword deny reaches superChat independently of its amount
        self.assertTrue(
            policy.is_suppressed(
                make_message(
                    2,
                    kind="superChat",
                    data={"text": "secret", "amountMilliCny": 30000, "durationSeconds": 60},
                )
            )
        )
        # gift threshold deny reaches ordinary gifts independently of identity
        self.assertTrue(policy.is_suppressed(gift(3, 10, user_id="user:bob")))

    def test_unmatched_message_is_not_suppressed(self):
        policy = self._policy(
            deny_user_ids={"user:alice"},
            deny_nicknames=["Carol"],
            keywords=["secret"],
            gift_threshold_milli_cny=100,
        )
        self.assertFalse(
            policy.is_suppressed(
                make_message(1, kind="danmaku", user_id="user:bob", name="Bob")
            )
        )


class DistributionHubFilteringTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_snapshot_and_store_stay_unfiltered(self):
        policy = FilteringPolicy(keywords=["secret"])
        store = SnapshotStore(max_messages=100)
        hub = DistributionHub(store=store, filter=policy.is_suppressed)
        hub.publish(make_message(1, kind="danmaku", data={"text": "a secret"}))
        hub.publish(make_message(2, kind="danmaku", data={"text": "hello"}))
        self.assertEqual([m.sequence for m in hub.snapshot()], [1, 2])
        self.assertEqual([m.sequence for m in store.list()], [1, 2])

    async def test_filtered_snapshot_excludes_suppressed(self):
        policy = FilteringPolicy(keywords=["secret"])
        hub = DistributionHub(filter=policy.is_suppressed, clock=_fixed_clock)
        hub.publish(make_message(1, kind="danmaku", data={"text": "hello"}))
        hub.publish(make_message(2, kind="danmaku", data={"text": "a secret"}))
        hub.publish(make_message(3, kind="danmaku", data={"text": "bye"}))
        self.assertEqual([m.sequence for m in hub.filtered_snapshot()], [1, 3])

    async def test_subscriber_queue_excludes_suppressed(self):
        policy = FilteringPolicy(keywords=["secret"])
        hub = DistributionHub(filter=policy.is_suppressed, clock=_fixed_clock)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, kind="danmaku", data={"text": "hello"}))
        hub.publish(make_message(2, kind="danmaku", data={"text": "a secret"}))
        hub.publish(make_message(3, kind="danmaku", data={"text": "bye"}))
        self.assertEqual((await subscriber.receive()).sequence, 1)
        self.assertEqual((await subscriber.receive()).sequence, 3)

    async def test_same_decision_for_snapshot_and_live_delivery(self):
        policy = FilteringPolicy(keywords=["secret"])
        hub = DistributionHub(filter=policy.is_suppressed, clock=_fixed_clock)
        subscriber = hub.subscribe()
        hub.publish(make_message(1, kind="danmaku", data={"text": "hello"}))
        hub.publish(make_message(2, kind="danmaku", data={"text": "a secret"}))
        hub.publish(make_message(3, kind="danmaku", data={"text": "bye"}))
        delivered = [(await subscriber.receive()).sequence for _ in range(2)]
        self.assertEqual(delivered, [1, 3])
        self.assertEqual(
            [m.sequence for m in hub.filtered_snapshot()], [1, 3]
        )

    async def test_filter_must_be_callable_or_none(self):
        with self.assertRaises(TypeError):
            DistributionHub(filter="not-callable")


class ServiceFilteringIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _config(self, **kwargs):
        return dataclasses.replace(ServiceConfig.default(), **kwargs)

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _url(self, service, path="/"):
        return f"http://127.0.0.1:{service.port}{path}"

    async def _start(self, policy, **kwargs):
        clock = kwargs.pop("clock", None)
        config = self._config(
            port=kwargs.pop("port", self._free_port()),
            cadence_milliseconds=kwargs.pop("cadence_milliseconds", 60000),
            **kwargs,
        )
        service = Service(config, policy=policy, clock=clock)
        await service.start()
        return service

    @staticmethod
    async def _wait_for_producer(service: Service) -> None:
        for _ in range(1000):
            if len(service.hub.snapshot()) >= 1:
                return
            await asyncio.sleep(0.005)

    async def test_service_exposes_shared_immutable_policy(self):
        policy = FilteringPolicy(deny_user_ids={"user:alice"})
        service = await self._start(policy)
        try:
            self.assertIs(service.policy, policy)
            self.assertEqual(service.policy.deny_user_ids, frozenset({"user:alice"}))
        finally:
            await service.stop()

    async def test_denied_user_is_suppressed_from_websocket(self):
        policy = FilteringPolicy(deny_user_ids={"user:alice"})
        service = await self._start(policy)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    await ws.receive()  # snapshot (producer's danmaku is denied)
                    await ws.send_str(HELLO)
                    service.hub.publish(make_message(100, user_id="user:alice"))
                    service.hub.publish(make_message(101, user_id="user:bob"))
                    frame = json.loads((await ws.receive()).data)
                    self.assertEqual(frame["type"], "message.created")
                    self.assertEqual(frame["payload"]["message"]["sequence"], 101)
                    # canonical host state still holds the suppressed message
                    self.assertIn(
                        100, [m.sequence for m in service.hub.snapshot()]
                    )
        finally:
            await service.stop()

    async def test_adjusted_gift_threshold_honored_over_websocket(self):
        policy = FilteringPolicy(
            deny_user_ids={"user:alice"}, gift_threshold_milli_cny=500
        )
        service = await self._start(policy)
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    await ws.receive()
                    await ws.send_str(HELLO)
                    # Distinct users so the two gifts do not aggregate; the
                    # below-threshold gift is suppressed and the at-threshold
                    # gift is delivered once its aggregate is finalized.
                    service.hub.publish(gift(200, 499, user_id="user:low"))
                    service.hub.publish(gift(201, 500, user_id="user:high"))
                    service.hub.finalize()
                    frame = json.loads((await ws.receive()).data)
                    self.assertEqual(frame["payload"]["message"]["sequence"], 201)
                    self.assertEqual(
                        frame["payload"]["message"]["data"]["totalAmountMilliCny"], 500
                    )
        finally:
            await service.stop()

    async def test_filtered_snapshot_excludes_suppressed_messages(self):
        policy = FilteringPolicy(deny_user_ids={"user:alice"})
        service = await self._start(policy, clock=_fixed_clock)
        try:
            await self._wait_for_producer(service)
            # Publish before connecting so both messages sit in the store.
            service.hub.publish(make_message(50, kind="danmaku", user_id="user:alice"))
            service.hub.publish(make_message(51, kind="danmaku", user_id="user:bob"))
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(self._url(service, "/ws")) as ws:
                    snapshot = json.loads((await ws.receive()).data)
                    self.assertEqual(snapshot["type"], "snapshot")
                    sequences = [
                        m["sequence"] for m in snapshot["payload"]["messages"]
                    ]
                    self.assertNotIn(50, sequences)
                    self.assertIn(51, sequences)
        finally:
            await service.stop()


if __name__ == "__main__":
    unittest.main()
