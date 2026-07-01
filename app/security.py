from __future__ import annotations

import base64
import hashlib
import hmac
from itertools import cycle


try:
    from cryptography.fernet import Fernet
except Exception:  # pragma: no cover - production dependencies install cryptography.
    Fernet = None  # type: ignore[assignment]


def _fernet_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    def __init__(self, secret: str):
        self.secret = secret or "dev-only-nocturne-encryption-key"
        self._key = hashlib.sha256(self.secret.encode("utf-8")).digest()
        self._fernet = Fernet(_fernet_key(self.secret)) if Fernet else None

    def encrypt(self, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        raw = value.encode("utf-8")
        if self._fernet:
            return "fernet:" + self._fernet.encrypt(raw).decode("utf-8")

        keystream = cycle(self._key)
        ciphertext = bytes(byte ^ next(keystream) for byte in raw)
        signature = hmac.new(self._key, ciphertext, hashlib.sha256).digest()
        return "fallback:" + base64.urlsafe_b64encode(signature + ciphertext).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith("fernet:"):
            if not self._fernet:
                raise RuntimeError("cryptography is required to decrypt stored Fernet secrets")
            return self._fernet.decrypt(value.removeprefix("fernet:").encode("utf-8")).decode("utf-8")
        if value.startswith("fallback:"):
            payload = base64.urlsafe_b64decode(value.removeprefix("fallback:").encode("utf-8"))
            signature, ciphertext = payload[:32], payload[32:]
            expected = hmac.new(self._key, ciphertext, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("encrypted value failed integrity check")
            keystream = cycle(self._key)
            plaintext = bytes(byte ^ next(keystream) for byte in ciphertext)
            return plaintext.decode("utf-8")
        raise ValueError("unknown encrypted value format")


def mask_secret(value: str | None, last4: str | None = None) -> str:
    suffix = last4 or ((value or "")[-4:] if value else "")
    return f"•••• {suffix}" if suffix else "연결 안 됨"


def stable_hash(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()
