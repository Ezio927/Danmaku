"""Public-behavior tests for the Open Live signing and redaction layer.

Coverage: secret and identity-code redaction in repr/str output, credential
validation, deterministic canonicalization, deterministic HMAC-SHA256 signing,
independent fixture expectations for the signature, header completeness, and
input validation.
"""

import base64
import hashlib
import hmac
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from danmaku.bilibili import (  # noqa: E402
    AppCredentials,
    IdentityCode,
    IdentityCodeError,
    REDACTED,
    SIGNATURE_METHOD,
    SIGNATURE_VERSION,
    Secret,
    canonical_string,
    content_md5,
    redact,
    sign_request,
)

ACCESS_KEY_ID = "synthetic-access-key-0001"
ACCESS_KEY_SECRET = "synthetic-access-key-secret-0001"
BODY = '{"code":"synthetic-identity-code-0001","app_id":12345}'
TIMESTAMP = 1735689600
NONCE = "synthetic-nonce-0001"


def _credentials():
    return AppCredentials(ACCESS_KEY_ID, Secret(ACCESS_KEY_SECRET))


def _expected_signature(secret: str, canonical: str) -> str:
    """Independently compute the documented HMAC-SHA256 signature."""
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")


def _sign():
    return sign_request(
        credentials=_credentials(),
        method="POST",
        path="/v2/app/start",
        body=BODY,
        timestamp=TIMESTAMP,
        nonce=NONCE,
    )


class SecretRedactionTests(unittest.TestCase):
    def test_repr_redacts_the_secret(self):
        secret = Secret("top-secret-value")
        self.assertNotIn("top-secret-value", repr(secret))
        self.assertIn(REDACTED, repr(secret))

    def test_str_redacts_the_secret(self):
        secret = Secret("top-secret-value")
        self.assertNotIn("top-secret-value", str(secret))
        self.assertEqual(str(secret), REDACTED)

    def test_value_exposes_the_raw_secret(self):
        self.assertEqual(Secret("top-secret-value").value, "top-secret-value")

    def test_non_string_secret_rejected(self):
        with self.assertRaises(TypeError):
            Secret(123)

    def test_redact_returns_fixed_placeholder(self):
        self.assertEqual(redact("anything"), REDACTED)


class CredentialTests(unittest.TestCase):
    def test_credentials_store_a_public_id_and_a_redacting_secret(self):
        cred = _credentials()
        self.assertEqual(cred.access_key_id, ACCESS_KEY_ID)
        self.assertNotIn(ACCESS_KEY_SECRET, repr(cred))
        self.assertIn(REDACTED, repr(cred))

    def test_empty_access_key_id_rejected(self):
        with self.assertRaises(TypeError):
            AppCredentials("", Secret("secret"))

    def test_control_character_in_access_key_id_rejected(self):
        with self.assertRaises(TypeError):
            AppCredentials("bad\u0000id", Secret("secret"))

    def test_non_secret_rejected(self):
        with self.assertRaises(TypeError):
            AppCredentials("id", "raw-secret")

    def test_empty_secret_rejected(self):
        with self.assertRaises(ValueError):
            AppCredentials("id", Secret(""))


class IdentityCodeTests(unittest.TestCase):
    def test_valid_code_constructed(self):
        code = IdentityCode("synthetic-identity-code-0001")
        self.assertEqual(code.value, "synthetic-identity-code-0001")

    def test_repr_and_str_redact_the_code(self):
        code = IdentityCode("synthetic-identity-code-0001")
        self.assertNotIn("synthetic-identity-code-0001", repr(code))
        self.assertNotIn("synthetic-identity-code-0001", str(code))
        self.assertEqual(str(code), REDACTED)

    def test_invalid_codes_rejected(self):
        for bad in ("", "x" * 257, "bad\u0000code", None, 123, True):
            with self.subTest(code=bad):
                with self.assertRaises(IdentityCodeError):
                    IdentityCode(bad)

    def test_boundary_length_accepted(self):
        self.assertEqual(IdentityCode("x" * 256).value, "x" * 256)


class ContentMd5Tests(unittest.TestCase):
    def test_known_md5_digest(self):
        self.assertEqual(
            content_md5(b"hello"), "5d41402abc4b2a76b9719d911017c592"
        )

    def test_non_bytes_rejected(self):
        with self.assertRaises(TypeError):
            content_md5("hello")


class CanonicalStringTests(unittest.TestCase):
    def test_exact_canonical_string(self):
        canonical = canonical_string(
            access_key_id=ACCESS_KEY_ID,
            content_md5="5d41402abc4b2a76b9719d911017c592",
            nonce=NONCE,
            timestamp=TIMESTAMP,
        )
        self.assertEqual(
            canonical,
            f"x-bili-accesskeyid:{ACCESS_KEY_ID}\n"
            "x-bili-content-md5:5d41402abc4b2a76b9719d911017c592\n"
            f"x-bili-signature-method:{SIGNATURE_METHOD}\n"
            f"x-bili-signature-nonce:{NONCE}\n"
            f"x-bili-signature-version:{SIGNATURE_VERSION}\n"
            f"x-bili-timestamp:{TIMESTAMP}",
        )

    def test_canonical_string_contains_no_secret(self):
        signed = _sign()
        self.assertNotIn(ACCESS_KEY_SECRET, signed.canonical_string)


class SignRequestTests(unittest.TestCase):
    def test_deterministic_signing(self):
        first = _sign()
        second = _sign()
        self.assertEqual(first.signature, second.signature)
        self.assertEqual(first.canonical_string, second.canonical_string)
        self.assertEqual(dict(first.headers), dict(second.headers))

    def test_signature_matches_independent_hmac(self):
        signed = _sign()
        expected = _expected_signature(ACCESS_KEY_SECRET, signed.canonical_string)
        self.assertEqual(signed.signature, expected)

    def test_signature_matches_golden_fixture(self):
        signed = _sign()
        # Golden values computed independently from the documented algorithm.
        self.assertEqual(signed.canonical_string, GOLDEN_CANONICAL)
        self.assertEqual(signed.signature, GOLDEN_SIGNATURE)

    def test_headers_are_complete_and_correct(self):
        signed = _sign()
        headers = dict(signed.headers)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(headers["x-bili-accesskeyid"], ACCESS_KEY_ID)
        self.assertEqual(
            headers["x-bili-content-md5"], content_md5(BODY.encode("utf-8"))
        )
        self.assertEqual(headers["x-bili-signature-method"], SIGNATURE_METHOD)
        self.assertEqual(headers["x-bili-signature-nonce"], NONCE)
        self.assertEqual(headers["x-bili-signature-version"], SIGNATURE_VERSION)
        self.assertEqual(headers["x-bili-timestamp"], str(TIMESTAMP))
        self.assertEqual(
            headers["Authorization"], f"{ACCESS_KEY_ID}:{signed.signature}"
        )

    def test_authorization_property(self):
        signed = _sign()
        self.assertEqual(
            signed.authorization, f"{ACCESS_KEY_ID}:{signed.signature}"
        )

    def test_repr_redacts_the_request_body(self):
        signed = _sign()
        self.assertNotIn("synthetic-identity-code-0001", repr(signed))
        self.assertIn(REDACTED, repr(signed))

    def test_summary_redacts_the_request_body(self):
        signed = _sign()
        self.assertNotIn("synthetic-identity-code-0001", signed.summary())

    def test_input_validation(self):
        for kwargs in (
            {"credentials": "not-credentials"},
            {"method": ""},
            {"method": "BAD\nMETHOD"},
            {"path": "no-leading-slash"},
            {"path": "bad\npath"},
            {"timestamp": True},
            {"timestamp": 1.5},
            {"timestamp": -1},
            {"nonce": ""},
            {"nonce": "bad\nnonce"},
            {"body": 123},
        ):
            with self.subTest(kwargs=kwargs):
                params = {
                    "credentials": _credentials(),
                    "method": "POST",
                    "path": "/v2/app/start",
                    "body": BODY,
                    "timestamp": TIMESTAMP,
                    "nonce": NONCE,
                }
                params.update(kwargs)
                with self.assertRaises((TypeError, ValueError)):
                    sign_request(**params)


# Golden fixture values (computed independently of the implementation).
GOLDEN_CANONICAL = (
    "x-bili-accesskeyid:synthetic-access-key-0001\n"
    "x-bili-content-md5:a6c6b4e37f42620d5ee1b8bcfdc5a600\n"
    "x-bili-signature-method:HMAC-SHA256\n"
    "x-bili-signature-nonce:synthetic-nonce-0001\n"
    "x-bili-signature-version:1.0\n"
    "x-bili-timestamp:1735689600"
)
GOLDEN_SIGNATURE = "cqvKgVIwH1r8uYVDMMQGU/+CZv9HD5M7+Om6TivvRqM="


if __name__ == "__main__":
    unittest.main()
