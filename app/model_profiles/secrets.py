import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class ModelSecretError(RuntimeError):
    pass


class ModelSecretService:
    def __init__(self, encryption_key: str):
        if not encryption_key or not encryption_key.strip():
            raise ModelSecretError("MODEL_SECRET_ENCRYPTION_KEY is required")
        self._key = _derive_key(encryption_key.strip())

    def encrypt_api_key(self, api_key: str) -> str:
        if not api_key:
            raise ModelSecretError("api_key cannot be empty")
        nonce = get_random_bytes(12)
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(api_key.encode("utf-8"))
        nonce_text = _b64encode(nonce)
        ciphertext_text = _b64encode(ciphertext)
        tag_text = _b64encode(tag)
        return f"v1:{nonce_text}:{ciphertext_text}:{tag_text}"

    def decrypt_api_key(self, encrypted_api_key: str) -> str:
        try:
            version, nonce_text, ciphertext_text, tag_text = encrypted_api_key.split(":", 3)
        except ValueError as exc:
            raise ModelSecretError("encrypted api key has invalid format") from exc
        if version != "v1":
            raise ModelSecretError("encrypted api key uses unsupported version")
        try:
            nonce = _b64decode(nonce_text)
            ciphertext = _b64decode(ciphertext_text)
            tag = _b64decode(tag_text)
            cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
            return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
        except Exception as exc:
            raise ModelSecretError("encrypted api key could not be decrypted") from exc


def _derive_key(encryption_key: str) -> bytes:
    decoded = _try_decode_key(encryption_key)
    if decoded is not None and len(decoded) == 32:
        return decoded
    raw = encryption_key.encode("utf-8")
    if len(raw) == 32:
        return raw
    return hashlib.sha256(raw).digest()


def _try_decode_key(value: str) -> bytes | None:
    padded = value + "=" * (-len(value) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(padded.encode("ascii"))
        except Exception:
            continue
    return None


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))
