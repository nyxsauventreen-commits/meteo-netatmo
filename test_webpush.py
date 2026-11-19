import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import webpush

# RFC 6979, appendix A.2.5: P-256 key and the signature of "sample" with SHA-256.
KEY = 0xC9AFA9D845BA75166B5C215767B1D6934E50C3DB36E89B127B8A622B120F6721
PUBLIC = (
    0x60FED4BA255A9D31C961EB74C6356D68C049B8923B61FA6CE669622E60F29FB6,
    0x7903FE1008B8BC99A41AE9E95628BC64F2F1B20C2D7E9F5177A3C294D4462299,
)
NONCE = 0xA6E3C57DD01ABE90086538398355DD4C3B17AA873382B0F24D6129493D8AAD60
R = 0xEFD48B2AACB6A8FD1140DD9CD45E81D69D2C877B56AAF991C34D0EA84EAF3716
S = 0xF7CB1C942D657C41D436C7A1B6E29F65F3E900DBB9AFF4064DC4AB2F843ACDA8
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


class Signature(unittest.TestCase):
    def test_public_key_of_the_rfc(self):
        self.assertTrue(webpush.on_curve(webpush.G))
        self.assertEqual(webpush.multiply(KEY), PUBLIC)

    def test_deterministic_nonce_and_signature_of_the_rfc(self):
        digest = webpush.hashlib.sha256(b"sample").digest()
        self.assertEqual(webpush.nonce(KEY, digest), NONCE)
        self.assertEqual(webpush.sign(KEY, b"sample"), (R, S))

    def test_verification(self):
        self.assertTrue(webpush.verify(PUBLIC, b"sample", R, S))
        self.assertFalse(webpush.verify(PUBLIC, b"sampl3", R, S))
        self.assertFalse(webpush.verify(PUBLIC, b"sample", R, S ^ 1))

    def test_order_times_g_is_infinity(self):
        self.assertIsNone(webpush.multiply(webpush.N))


class Keys(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "vapid.json"

    def test_created_once_then_kept(self):
        first = webpush.Keys(self.path)
        second = webpush.Keys(self.path)
        self.assertEqual(first.application_key, second.application_key)
        self.assertEqual(len(webpush.unb64url(first.application_key)), 65)

    def test_token_is_a_valid_es256_jwt(self):
        keys = webpush.Keys(self.path)
        token = keys.token("https://fcm.googleapis.com", "https://meteo.example", now=1000)
        header, claims, signature = token.split(".")
        self.assertEqual(json.loads(webpush.unb64url(header)), {"typ": "JWT", "alg": "ES256"})
        self.assertEqual(json.loads(webpush.unb64url(claims)),
                         {"aud": "https://fcm.googleapis.com", "exp": 1000 + 12 * 3600, "sub": "https://meteo.example"})
        raw = webpush.unb64url(signature)
        point = (int.from_bytes(keys.public[1:33], "big"), int.from_bytes(keys.public[33:], "big"))
        self.assertTrue(webpush.verify(point, f"{header}.{claims}".encode(),
                                       int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")))


class Sending(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.keys = webpush.Keys(Path(folder.name) / "vapid.json")

    def test_only_the_push_services_of_the_browsers(self):
        for good in (ENDPOINT, "https://updates.push.services.mozilla.com/wpush/v2/x",
                     "https://web.push.apple.com/QK", "https://wns2-par02p.notify.windows.com/w/?token=x"):
            self.assertTrue(webpush.is_push_address(good), good)
        for bad in ("http://fcm.googleapis.com/x", "https://fcm.googleapis.com.evil.example/x",
                    "https://evil.example/fcm.googleapis.com", "https://127.0.0.1/x",
                    "https://fcm.googleapis.com:8443/x", "file:///etc/passwd", "https://" + "a" * 1000):
            self.assertFalse(webpush.is_push_address(bad), bad)

    def test_empty_signed_message(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            webpush.send(self.keys, ENDPOINT, "https://meteo.example", urgent=True)
        request = urlopen.call_args[0][0]
        self.assertEqual((request.full_url, request.data, request.get_method()), (ENDPOINT, b"", "POST"))
        self.assertEqual(request.get_header("Urgency"), "high")
        self.assertTrue(request.get_header("Authorization").startswith("vapid t="))
        self.assertTrue(request.get_header("Authorization").endswith(f", k={self.keys.application_key}"))

    def test_expired_subscription(self):
        gone = urllib.error.HTTPError(ENDPOINT, 410, "Gone", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=gone):
            with self.assertRaises(webpush.Expired):
                webpush.send(self.keys, ENDPOINT, "https://meteo.example")

    def test_other_errors(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(webpush.PushError) as caught:
                webpush.send(self.keys, ENDPOINT, "https://meteo.example")
        self.assertNotIsInstance(caught.exception, webpush.Expired)


if __name__ == "__main__":
    unittest.main()
