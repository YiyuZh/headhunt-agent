from typing import Any

import httpx

from app.gateways.url_safety import AddressResolver, BaseUrlSafetyError, validate_https_base_url


class EmbeddingGatewayError(RuntimeError):
    pass


class OpenAIEmbeddingGateway:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
        base_url_resolver: AddressResolver | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.base_url_resolver = base_url_resolver
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def embed_texts(self, texts: list[str], purpose: str) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.post(
            f"{self._safe_base_url()}/v1/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "input": texts,
                "user": purpose[:64],
            },
        )
        if response.status_code >= 400:
            raise EmbeddingGatewayError(f"OpenAI embeddings API error: HTTP {response.status_code}")
        payload = response.json()
        return _extract_embeddings(payload, expected_count=len(texts))

    def _safe_base_url(self) -> str:
        try:
            return validate_https_base_url(
                self.base_url,
                trusted_public_hosts={"api.openai.com"},
                resolver=self.base_url_resolver,
            )
        except BaseUrlSafetyError as exc:
            raise EmbeddingGatewayError(f"Unsafe OpenAI embedding base_url: {exc}") from exc


def _extract_embeddings(payload: dict[str, Any], *, expected_count: int) -> list[list[float]]:
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise EmbeddingGatewayError("embedding response data length does not match input length")
    embeddings: list[list[float]] = []
    for row in sorted(rows, key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0):
        if not isinstance(row, dict) or not isinstance(row.get("embedding"), list):
            raise EmbeddingGatewayError("embedding response row missing embedding")
        embeddings.append([float(value) for value in row["embedding"]])
    return embeddings
