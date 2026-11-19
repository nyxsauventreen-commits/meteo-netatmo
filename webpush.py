"""Notifications sent by the site itself (Web Push), without an extra app and without a library.

The browser gives an address at its push service (Google, Mozilla, Apple or Microsoft). The site
posts an empty message there, signed with its own key (VAPID, RFC 8292): an empty message needs
no encryption. On the phone or the PC, the page's service worker then asks the site for the text
and shows it.

The signature is ECDSA P-256 with SHA-256 (ES256), with the deterministic nonce of RFC 6979.
"""
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# NIST P-256
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)
TIMEOUT = 15
TTL = 6 * 3600  # a message older than this is not worth showing
# The push services of the browsers: the site never posts anywhere else.
PUSH_HOSTS = (
    "fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
    "web.push.apple.com", "notify.windows.com",
)


class PushError(Exception):
    """A message could not be sent."""


class Expired(PushError):
    """The browser gave up this subscription: forget it."""


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def on_curve(point):
    x, y = point
    return (y * y - (x * x * x + A * x + B)) % P == 0


def add(p, q):
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % P == 0:
        return None
    if p == q:
        slope = (3 * p[0] * p[0] + A) * pow(2 * p[1], -1, P) % P
    else:
        slope = (q[1] - p[1]) * pow(q[0] - p[0], -1, P) % P
    x = (slope * slope - p[0] - q[0]) % P
    return x, (slope * (p[0] - x) - p[1]) % P


def multiply(k, point=G):
    """k × point with a Montgomery ladder: the same steps whatever the bits of k."""
    low, high = None, point
    for bit in bin(k)[2:]:
        if bit == "1":
            low, high = add(low, high), add(high, high)
        else:
            low, high = add(low, low), add(low, high)
    return low


def public_bytes(point):
    return b"\x04" + point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")


def new_private_key():
    while True:
        key = int.from_bytes(os.urandom(32), "big")
        if 1 <= key < N:
            return key


def nonce(key, digest):
    """RFC 6979, section 3.2, for SHA-256 and a 256-bit order."""
    x = key.to_bytes(32, "big")
    h = (int.from_bytes(digest, "big") % N).to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 1 <= candidate < N:
            return candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign(key, message):
    """Return (r, s) for message (bytes)."""
    digest = hashlib.sha256(message).digest()
    e = int.from_bytes(digest, "big")
    k = nonce(key, digest)
    while True:
        r = multiply(k)[0] % N
        s = pow(k, -1, N) * (e + r * key) % N
        if r and s:
            return r, s
        k = (k + 1) % N or 1  # never happens in practice


def verify(point, message, r, s):
    if not (1 <= r < N and 1 <= s < N):
        return False
    e = int.from_bytes(hashlib.sha256(message).digest(), "big")
    w = pow(s, -1, N)
    found = add(multiply(e * w % N), multiply(r * w % N, point))
    return found is not None and found[0] % N == r


class Keys:
    """The site's VAPID key pair, created once and kept in a file of the data folder."""

    def __init__(self, path):
        self.path = path
        try:
            self.private = int(json.loads(path.read_text(encoding="utf-8"))["private"], 16)
        except (OSError, ValueError, KeyError):
            self.private = new_private_key()
            path.write_text(json.dumps({"private": f"{self.private:064x}"}), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        self.public = public_bytes(multiply(self.private))

    @property
    def application_key(self):
        """What the page gives to pushManager.subscribe()."""
        return b64url(self.public)

    def token(self, audience, subject, now=None):
        header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64url(json.dumps({
            "aud": audience, "exp": int(now or time.time()) + 12 * 3600, "sub": subject,
        }).encode())
        r, s = sign(self.private, f"{header}.{claims}".encode())
        return f"{header}.{claims}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def is_push_address(endpoint):
    """Only the push services of the browsers, over HTTPS."""
    try:
        url = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return False
    host = (url.hostname or "").lower()
    return (
        url.scheme == "https" and len(endpoint) <= 1000 and url.port in (None, 443)
        and any(host == name or host.endswith("." + name) for name in PUSH_HOSTS)
    )


def send(keys, endpoint, subject, urgent=False):
    """Post an empty message: the service worker then fetches the text from the site."""
    if not is_push_address(endpoint):
        raise Expired("Adresse de notification refusée")
    url = urllib.parse.urlsplit(endpoint)
    audience = f"{url.scheme}://{url.netloc}"
    request = urllib.request.Request(endpoint, data=b"", method="POST", headers={
        "TTL": str(TTL),
        "Urgency": "high" if urgent else "normal",
        "Authorization": f"vapid t={keys.token(audience, subject)}, k={keys.application_key}",
    })
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read()
    except urllib.error.HTTPError as err:
        if err.code in (404, 410):
            raise Expired("Abonnement expiré") from None
        raise PushError(f"Service de notification : erreur {err.code}") from None
    except OSError as err:
        raise PushError(f"Service de notification injoignable : {err}") from None
