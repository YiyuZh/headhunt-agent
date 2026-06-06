from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class ReadinessCheck(BaseModel):
    name: str
    category: str
    status: Literal["ok", "missing", "warning", "error"]
    message: str
    required_for: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)


class ReadinessResponse(BaseModel):
    status: str
    checks: dict[str, bool | str | int | None]
    details: list[ReadinessCheck] = Field(default_factory=list)
    missing_required: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


class VersionResponse(BaseModel):
    app_name: str
    app_version: str
