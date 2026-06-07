from time import time

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.core.config import Settings

DISCORD_SIGNATURE_HEADER = "X-Signature-Ed25519"
DISCORD_TIMESTAMP_HEADER = "X-Signature-Timestamp"


class DiscordSignatureConfigurationError(RuntimeError):
    pass


class DiscordSignatureVerificationError(RuntimeError):
    pass


class DiscordSignatureVerifier:
    def __init__(self, settings: Settings):
        public_key = (settings.discord_public_key or "").strip()
        if not public_key:
            raise DiscordSignatureConfigurationError("DISCORD_PUBLIC_KEY is required")
        self.max_age_seconds = settings.discord_signature_max_age_seconds
        try:
            self.verify_key = VerifyKey(bytes.fromhex(public_key))
        except ValueError as exc:
            raise DiscordSignatureConfigurationError("DISCORD_PUBLIC_KEY must be hex") from exc

    def verify(self, *, raw_body: bytes, headers) -> None:
        signature = headers.get(DISCORD_SIGNATURE_HEADER)
        timestamp = headers.get(DISCORD_TIMESTAMP_HEADER)
        if not signature or not timestamp:
            raise DiscordSignatureVerificationError("Discord signature headers are required")
        try:
            timestamp_seconds = int(timestamp)
        except ValueError as exc:
            raise DiscordSignatureVerificationError(
                "Discord timestamp must be unix seconds"
            ) from exc
        if abs(time() - timestamp_seconds) > self.max_age_seconds:
            raise DiscordSignatureVerificationError("Discord interaction timestamp is stale")
        try:
            signature_bytes = bytes.fromhex(signature)
        except ValueError as exc:
            raise DiscordSignatureVerificationError("Discord signature must be hex") from exc
        try:
            self.verify_key.verify(timestamp.encode("utf-8") + raw_body, signature_bytes)
        except BadSignatureError as exc:
            raise DiscordSignatureVerificationError(
                "Invalid Discord interaction signature"
            ) from exc
