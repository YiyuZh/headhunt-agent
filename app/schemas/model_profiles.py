from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

ModelProvider = Literal["openai", "deepseek"]
ModelUsage = Literal["chat", "embedding"]
ModelProfileStatus = Literal["active", "revoked"]
ModelTestStatus = Literal["untested", "ok", "failed"]


class CreateModelProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: ModelProvider
    model_name: str = Field(min_length=1, max_length=200)
    api_key: SecretStr
    display_name: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    usage: ModelUsage = "chat"
    make_default: bool = True


class ModelProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: str | None = None
    guild_id: str
    user_id: str
    provider: ModelProvider
    model_name: str
    display_name: str
    base_url: str | None = None
    usage: ModelUsage
    status: ModelProfileStatus
    is_default: bool = False
    last_test_status: ModelTestStatus = "untested"
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ModelTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: UUID
    provider: ModelProvider
    model_name: str
    status: Literal["ok", "failed"]
    message: str
