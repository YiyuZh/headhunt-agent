from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_name: str = "AI Headhunter War Room"
    app_version: str = "0.1.0"
    internal_admin_api_key: SecretStr | None = None

    database_url: str = "postgresql+psycopg://lietou:lietou@localhost:5432/lietou"
    checkpoint_db_url: str = "postgresql+psycopg://lietou:lietou@localhost:5432/lietou"

    vector_store_provider: Literal["pgvector", "qdrant", "milvus"] = "pgvector"
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int = Field(default=1536, gt=0)

    channel_gateway_provider: str = "discord"
    discord_public_key: str | None = None
    discord_bot_token: SecretStr | None = None
    discord_application_id: str | None = None
    discord_allowed_guild_ids: str | None = None
    discord_allowed_channel_ids: str | None = None
    discord_interaction_callback_path: str = "/discord/interactions"

    feishu_base_url: str = "https://open.feishu.cn"
    feishu_app_id: str | None = None
    feishu_app_secret: SecretStr | None = None
    feishu_verification_token: SecretStr | None = None
    feishu_encrypt_key: SecretStr | None = None
    feishu_default_chat_id: str | None = None
    feishu_bitable_app_token: str | None = None
    feishu_bitable_requisition_table_id: str | None = None
    feishu_bitable_candidate_table_id: str | None = None
    feishu_bitable_talent_map_table_id: str | None = None
    feishu_bitable_report_table_id: str | None = None

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None

    outbox_worker_id: str | None = None
    outbox_poll_seconds: float = Field(default=2.0, gt=0)

    def readiness(self) -> dict[str, bool | str | int | None]:
        return {
            "database_configured": self.database_url.startswith("postgresql+psycopg://"),
            "checkpoint_configured": self.checkpoint_db_url.startswith("postgresql+psycopg://"),
            "vector_store_provider": self.vector_store_provider,
            "embedding_dimension": self.embedding_dimension,
            "embedding_configured": bool(
                self.embedding_provider == "openai" and self.embedding_model and self.llm_api_key
            ),
            "internal_admin_configured": bool(self.internal_admin_api_key),
            "channel_gateway_provider": self.channel_gateway_provider,
            "discord_app_configured": bool(
                self.discord_public_key
                and self.discord_bot_token
                and self.discord_application_id
            ),
            "discord_allowlist_configured": bool(
                self.discord_allowed_guild_ids and self.discord_allowed_channel_ids
            ),
            "discord_interactions_implemented": False,
            "feishu_app_configured": bool(self.feishu_app_id and self.feishu_app_secret),
            "feishu_callback_configured": bool(
                self.feishu_verification_token and self.feishu_encrypt_key
            ),
            "feishu_war_room_configured": bool(self.feishu_default_chat_id),
            "feishu_bitable_configured": bool(
                self.feishu_bitable_app_token
                and self.feishu_bitable_requisition_table_id
                and self.feishu_bitable_candidate_table_id
                and self.feishu_bitable_talent_map_table_id
                and self.feishu_bitable_report_table_id
            ),
            "llm_configured": bool(self.llm_provider and self.llm_model and self.llm_api_key),
            "outbox_worker_configured": self.outbox_poll_seconds > 0,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
