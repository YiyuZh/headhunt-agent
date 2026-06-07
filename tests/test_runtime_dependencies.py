import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.runtime.dependencies import (
    RuntimeDependencyError,
    build_agent_harness,
    build_embedding_gateway,
    build_llm_gateway,
    build_runtime_graph_factory,
)


class FakeSession:
    pass


def settings(**overrides) -> Settings:
    data = {
        "llm_provider": "openai_responses",
        "llm_model": "gpt-test",
        "llm_api_key": SecretStr("sk-test"),
        "model_secret_encryption_key": SecretStr("model-secret"),
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_api_key": SecretStr("embed-test"),
    }
    data.update(overrides)
    return Settings(**data)


def test_runtime_dependency_factory_builds_graph_factory_with_harness() -> None:
    factory = build_runtime_graph_factory(session=FakeSession(), settings=settings())

    assert factory.agent_harness is not None
    assert factory.action_gate is not None
    assert factory.action_executor is not None


def test_build_agent_harness_uses_real_gateway_factories() -> None:
    harness = build_agent_harness(session=FakeSession(), settings=settings())

    assert harness.llm_gateway is not None
    assert harness.memory_gateway is not None


def test_llm_gateway_requires_configured_provider_and_key() -> None:
    with pytest.raises(RuntimeDependencyError):
        build_llm_gateway(settings(llm_provider="", model_secret_encryption_key=None))

    with pytest.raises(RuntimeDependencyError):
        build_llm_gateway(settings(llm_api_key=None, model_secret_encryption_key=None))


def test_embedding_gateway_requires_openai_provider_and_model() -> None:
    with pytest.raises(RuntimeDependencyError):
        build_embedding_gateway(settings(embedding_provider=""))

    with pytest.raises(RuntimeDependencyError):
        build_embedding_gateway(settings(embedding_model=None))


def test_agent_harness_skips_memory_when_embedding_profile_is_missing() -> None:
    harness = build_agent_harness(
        session=FakeSession(),
        settings=settings(embedding_api_key=None, openai_api_key=None, llm_api_key=None),
    )

    assert harness.memory_gateway.__class__.__name__ == "UserModelMemoryGatewayRouter"
