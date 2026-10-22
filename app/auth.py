"""Mots de passe (bcrypt), sessions signées (HMAC-SHA256) et anti brute-force."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time

import bcrypt

COOKIE_NAME = "fev_session"
MIN_PASSWORD_LENGTH = 8


def _prehash(password: str) -> bytes:
    # bcrypt ignore (ou refuse) au-delà de 72 octets : on hache d'abord en SHA-256.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("ascii")


# Haché factice : vérifier un identifiant inconnu coûte autant qu'un vrai (pas d'oracle temporel).
_DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str | None) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), (hashed or _DUMMY_HASH).encode("ascii")) and hashed is not None
    except ValueError:
        return False


def fingerprint(password_hash: str | None) -> str:
    """Empreinte du haché : changer le mot de passe invalide toutes les sessions existantes."""
    return hashlib.sha256((password_hash or "").encode()).hexdigest()[:16]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionCodec:
    def __init__(self, secret: str):
        self._key = hashlib.sha256(("fev-session:" + secret).encode()).digest()

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())

    def issue(self, username: str, fp: str, ttl_s: int) -> str:
        body = _b64(json.dumps({"u": username, "f": fp, "e": int(time.time()) + ttl_s}, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def verify(self, token: str | None) -> dict | None:
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(body)):
            return None
        try:
            payload = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("e", 0) < time.time():
            return None
        return payload


class LoginThrottle:
    """Délai progressif : 3 essais libres, puis 1 s, 2 s, 4 s… jusqu'à 5 min par clé."""

    FREE_ATTEMPTS = 3
    MAX_DELAY = 300.0
    FORGET_AFTER = 3600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[int, float, float]] = {}  # clé -> (échecs, bloqué_jusqu'à, dernier)

    def retry_after(self, *keys: str) -> float:
        now = time.time()
        with self._lock:
            waits = [self._data[k][1] - now for k in keys if k in self._data]
        return max([0.0, *waits])

    def failure(self, *keys: str) -> float:
        now = time.time()
        delay = 0.0
        with self._lock:
            self._prune(now)
            for k in keys:
                n = self._data.get(k, (0, 0.0, 0.0))[0] + 1
                d = 0.0 if n < self.FREE_ATTEMPTS else min(self.MAX_DELAY, 2.0 ** (n - self.FREE_ATTEMPTS))
                self._data[k] = (n, now + d, now)
                delay = max(delay, d)
        return delay

    def success(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._data.pop(k, None)

    def _prune(self, now: float) -> None:
        if len(self._data) < 1000:
            return
        for k in [k for k, v in self._data.items() if now - v[2] > self.FORGET_AFTER]:
            del self._data[k]
