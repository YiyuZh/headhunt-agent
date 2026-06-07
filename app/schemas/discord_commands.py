from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DiscordCommandRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guild_ids: list[str] | None = Field(default=None, max_length=20)
    include_global: bool = False
    dry_run: bool = False


class DiscordCommandRegistrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["guild", "global"]
    target_id: str | None = None
    command_count: int
    status_code: int
    route: str


class DiscordCommandRegisterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    command_names: list[str]
    registered: list[DiscordCommandRegistrationResult] = Field(default_factory=list)
    message: str
