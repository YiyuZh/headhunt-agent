import pytest

from app.gateways.embeddings import EmbeddingGatewayError, OpenAIEmbeddingGateway


class FakeHttpResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, response):
        self.response = response
        self.posts = []

    def post(self, url, headers, json):
        self.posts.append((url, headers, json))
        return self.response


def test_openai_embedding_gateway_posts_batch_and_preserves_order() -> None:
    client = FakeHttpClient(
        FakeHttpResponse(
            {
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            }
        )
    )
    gateway = OpenAIEmbeddingGateway(
        api_key="sk-test",
        model="text-embedding-3-small",
        client=client,
    )

    embeddings = gateway.embed_texts(["a", "b"], purpose="memory_retrieval:StrategyDraftAgent")

    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    url, headers, body = client.posts[0]
    assert url == "https://api.openai.com/v1/embeddings"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == ["a", "b"]


def test_openai_embedding_gateway_rejects_wrong_response_length() -> None:
    gateway = OpenAIEmbeddingGateway(
        api_key="sk-test",
        model="text-embedding-3-small",
        client=FakeHttpClient(FakeHttpResponse({"data": []})),
    )

    with pytest.raises(EmbeddingGatewayError):
        gateway.embed_texts(["a"], purpose="memory_update")
