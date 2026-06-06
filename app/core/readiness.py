from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from app.core.config import Settings
from app.schemas.system import ReadinessCheck


@dataclass(frozen=True)
class ReadinessReport:
    status: str
    checks: dict[str, bool | str | int | None]
    details: list[ReadinessCheck]
    missing_required: list[str]
    next_steps: list[str]


def build_readiness_report(settings: Settings) -> ReadinessReport:
    details = [
        _check(
            name="database_url",
            category="postgres",
            ok=settings.database_url.startswith("postgresql+psycopg://"),
            message_ok="DATABASE_URL uses the first-version PostgreSQL psycopg path.",
            message_missing=(
                "DATABASE_URL must use postgresql+psycopg:// for the first-version runtime."
            ),
            env_vars=["DATABASE_URL"],
            required_for=["api", "repositories", "outbox"],
        ),
        _check(
            name="checkpoint_db_url",
            category="postgres",
            ok=settings.checkpoint_db_url.startswith("postgresql+psycopg://"),
            message_ok="CHECKPOINT_DB_URL uses the PostgreSQL checkpointer path.",
            message_missing=(
                "CHECKPOINT_DB_URL must use postgresql+psycopg:// before real interrupt/resume."
            ),
            env_vars=["CHECKPOINT_DB_URL"],
            required_for=["langgraph_checkpoint", "interrupt_resume"],
        ),
        _check(
            name="vector_store",
            category="memory",
            ok=settings.vector_store_provider == "pgvector",
            message_ok="VECTOR_STORE_PROVIDER is pgvector.",
            message_missing="VECTOR_STORE_PROVIDER must be pgvector for the first-version runtime.",
            env_vars=["VECTOR_STORE_PROVIDER"],
            required_for=["memory_gateway"],
        ),
        _check(
            name="embedding_runtime",
            category="memory",
            ok=_present(settings.embedding_provider)
            and settings.embedding_provider == "openai"
            and _present(settings.embedding_model)
            and _present(settings.llm_api_key),
            message_ok="OpenAI embedding runtime is configured.",
            message_missing=(
                "Set EMBEDDING_PROVIDER=openai, EMBEDDING_MODEL, and LLM_API_KEY before "
                "real MemoryGateway retrieval/write."
            ),
            env_vars=["EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "LLM_API_KEY"],
            required_for=["memory_gateway", "run_memory_vectorization"],
        ),
        _check(
            name="internal_admin",
            category="security",
            ok=_present(settings.internal_admin_api_key),
            message_ok="INTERNAL_ADMIN_API_KEY is configured for internal control APIs.",
            message_missing=(
                "Set INTERNAL_ADMIN_API_KEY before exposing /ready, /tasks, /approvals, "
                "/threads, /runs, or /council on a server."
            ),
            env_vars=["INTERNAL_ADMIN_API_KEY"],
            required_for=["internal_control_api", "manual_approval", "inspection"],
        ),
        _check(
            name="llm_runtime",
            category="agent",
            ok=(settings.llm_provider or "").lower() in {"openai", "openai_responses"}
            and _present(settings.llm_model)
            and _present(settings.llm_api_key),
            message_ok="OpenAI Responses LLM runtime is configured.",
            message_missing=(
                "Set LLM_PROVIDER=openai_responses, LLM_MODEL, and LLM_API_KEY before "
                "real AgentHarness graph dispatch."
            ),
            env_vars=["LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY"],
            required_for=["agent_harness", "graph_dispatch"],
        ),
        _check(
            name="discord_app",
            category="discord",
            ok=_present(settings.discord_public_key)
            and _present(settings.discord_bot_token)
            and _present(settings.discord_application_id),
            message_ok="Discord application credentials are configured.",
            message_missing=(
                "Set DISCORD_PUBLIC_KEY, DISCORD_BOT_TOKEN, and DISCORD_APPLICATION_ID "
                "before real Discord interactions are enabled. /discord/interactions is "
                "still not implemented in the current code."
            ),
            env_vars=[
                "DISCORD_PUBLIC_KEY",
                "DISCORD_BOT_TOKEN",
                "DISCORD_APPLICATION_ID",
            ],
            required_for=["discord_interactions", "discord_war_room"],
            required=False,
        ),
        _check(
            name="discord_allowlist",
            category="discord",
            ok=_present(settings.discord_allowed_guild_ids)
            and _present(settings.discord_allowed_channel_ids),
            message_ok="Discord guild/channel allowlist is configured.",
            message_missing=(
                "Set DISCORD_ALLOWED_GUILD_IDS and DISCORD_ALLOWED_CHANNEL_IDS before "
                "real Discord use. This remains a deferred implementation warning until "
                "/discord/interactions exists."
            ),
            env_vars=["DISCORD_ALLOWED_GUILD_IDS", "DISCORD_ALLOWED_CHANNEL_IDS"],
            required_for=["discord_interactions", "discord_war_room"],
            required=False,
        ),
        _check(
            name="discord_interactions_implementation",
            category="discord",
            ok=False,
            message_ok="Discord interactions endpoint is implemented.",
            message_missing=(
                "/discord/interactions is not implemented yet; Discord real integration "
                "remains unverified."
            ),
            env_vars=[],
            required_for=["discord_interactions"],
            required=False,
        ),
        _check(
            name="feishu_openapi",
            category="feishu",
            ok=_present(settings.feishu_app_id) and _present(settings.feishu_app_secret),
            message_ok="Deferred Feishu OpenAPI adapter credentials are configured.",
            message_missing=(
                "Feishu/Bitable is a deferred adapter, not the first-version main path. "
                "Set FEISHU_APP_ID and FEISHU_APP_SECRET only when enabling that adapter."
            ),
            env_vars=["FEISHU_APP_ID", "FEISHU_APP_SECRET"],
            required_for=["card_send", "card_update", "bitable_write"],
            required=False,
        ),
        _check(
            name="feishu_callbacks",
            category="feishu",
            ok=_present(settings.feishu_verification_token)
            and _present(settings.feishu_encrypt_key),
            message_ok=(
                "Deferred Feishu callback verification token and encrypt key are configured."
            ),
            message_missing=(
                "Feishu callbacks are deferred. Set FEISHU_VERIFICATION_TOKEN and "
                "FEISHU_ENCRYPT_KEY only when enabling real Feishu event/card callbacks."
            ),
            env_vars=["FEISHU_VERIFICATION_TOKEN", "FEISHU_ENCRYPT_KEY"],
            required_for=["feishu_events", "feishu_card_actions"],
            required=False,
        ),
        _check(
            name="feishu_war_room",
            category="feishu",
            ok=_present(settings.feishu_default_chat_id),
            message_ok="Deferred Feishu War Room chat_id is configured.",
            message_missing=(
                "Feishu War Room cards are deferred. Set FEISHU_DEFAULT_CHAT_ID only when "
                "enabling the Feishu adapter."
            ),
            env_vars=["FEISHU_DEFAULT_CHAT_ID"],
            required_for=["war_room_cards"],
            required=False,
        ),
        _check(
            name="feishu_bitable",
            category="feishu",
            ok=all(
                _present(value)
                for value in [
                    settings.feishu_bitable_app_token,
                    settings.feishu_bitable_requisition_table_id,
                    settings.feishu_bitable_candidate_table_id,
                    settings.feishu_bitable_talent_map_table_id,
                    settings.feishu_bitable_report_table_id,
                ]
            ),
            message_ok="Deferred Feishu Bitable app_token and table IDs are configured.",
            message_missing=(
                "Feishu Bitable is deferred. Set FEISHU_BITABLE_APP_TOKEN and "
                "FEISHU_BITABLE_*_TABLE_ID values only when enabling that adapter."
            ),
            env_vars=[
                "FEISHU_BITABLE_APP_TOKEN",
                "FEISHU_BITABLE_REQUISITION_TABLE_ID",
                "FEISHU_BITABLE_CANDIDATE_TABLE_ID",
                "FEISHU_BITABLE_TALENT_MAP_TABLE_ID",
                "FEISHU_BITABLE_REPORT_TABLE_ID",
            ],
            required_for=["bitable_write"],
            required=False,
        ),
        _check(
            name="outbox_worker",
            category="worker",
            ok=settings.outbox_poll_seconds > 0,
            message_ok="Durable outbox worker polling is configured.",
            message_missing="OUTBOX_POLL_SECONDS must be greater than 0.",
            env_vars=["OUTBOX_WORKER_ID", "OUTBOX_POLL_SECONDS"],
            required_for=["graph_dispatch", "resume", "card_send", "bitable_write"],
        ),
    ]
    missing_required = _missing_env_vars(details)
    status = _status_for(details)
    return ReadinessReport(
        status=status,
        checks=settings.readiness(),
        details=details,
        missing_required=missing_required,
        next_steps=_next_steps(details),
    )


def _present(value: Any) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


def _check(
    *,
    name: str,
    category: str,
    ok: bool,
    message_ok: str,
    message_missing: str,
    env_vars: list[str],
    required_for: list[str],
    required: bool = True,
) -> ReadinessCheck:
    return ReadinessCheck(
        name=name,
        category=category,
        status="ok" if ok else ("missing" if required else "warning"),
        message=message_ok if ok else message_missing,
        required_for=required_for,
        env_vars=env_vars,
    )


def _missing_env_vars(details: Iterable[ReadinessCheck]) -> list[str]:
    seen: set[str] = set()
    missing: list[str] = []
    for detail in details:
        if detail.status not in {"missing", "error"}:
            continue
        for env_var in detail.env_vars:
            if env_var not in seen:
                seen.add(env_var)
                missing.append(env_var)
    return missing


def _status_for(details: Iterable[ReadinessCheck]) -> str:
    statuses = {detail.status for detail in details}
    if statuses & {"missing", "error"}:
        return "not_ready"
    if "warning" in statuses:
        return "degraded"
    return "ok"


def _next_steps(details: Iterable[ReadinessCheck]) -> list[str]:
    steps: list[str] = []
    for detail in details:
        if detail.status == "ok":
            continue
        steps.append(detail.message)
    return steps
