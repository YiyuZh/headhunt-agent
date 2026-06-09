import argparse
import json
from dataclasses import asdict, dataclass
from typing import Literal

import httpx

from app.core.config import Settings, get_settings
from app.feishu.gateways import FeishuAuthProvider, FeishuHttpBitableGateway, FeishuHttpGateway
from app.gateways.embeddings import OpenAIEmbeddingGateway
from app.gateways.llm import DeepSeekChatCompletionsLLMGateway, OpenAIResponsesLLMGateway
from app.schemas.common import CouncilMode
from app.schemas.context import ContextPack

SmokeStatus = Literal["ok", "failed", "skipped"]
ReportStatus = Literal["ok", "failed", "partial"]


@dataclass(frozen=True)
class ExternalSmokeCheck:
    name: str
    status: SmokeStatus
    message: str


@dataclass(frozen=True)
class ExternalSmokeReport:
    status: ReportStatus
    checks: list[ExternalSmokeCheck]


def run_external_smoke_check(
    *,
    settings: Settings | None = None,
    feishu_client: httpx.Client | None = None,
    openai_client: httpx.Client | None = None,
    include_feishu: bool = True,
    include_bitable: bool = True,
    include_openai: bool = True,
    include_llm: bool = True,
    include_deepseek: bool = False,
) -> ExternalSmokeReport:
    resolved_settings = settings or get_settings()
    checks: list[ExternalSmokeCheck] = []

    if include_feishu:
        checks.extend(_run_feishu_checks(resolved_settings, feishu_client, include_bitable))
    else:
        checks.append(_skipped("feishu", "Feishu smoke checks skipped by CLI flag."))

    if include_openai:
        checks.extend(_run_openai_checks(resolved_settings, openai_client, include_llm))
    else:
        checks.append(_skipped("openai", "OpenAI smoke checks skipped by CLI flag."))

    if include_deepseek:
        checks.append(_run_deepseek_check(resolved_settings, openai_client))

    return _report(checks)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run Feishu and OpenAI external API smoke checks."
    )
    parser.add_argument("--skip-feishu", action="store_true", help="Skip Feishu token/chat checks.")
    parser.add_argument("--skip-bitable", action="store_true", help="Skip Bitable read checks.")
    parser.add_argument("--skip-openai", action="store_true", help="Skip OpenAI checks.")
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="Skip the OpenAI Responses LLM smoke call but still check embeddings.",
    )
    parser.add_argument(
        "--include-deepseek",
        action="store_true",
        help="Also run a DeepSeek chat JSON smoke call with DEEPSEEK_API_KEY.",
    )
    args = parser.parse_args(argv)

    report = run_external_smoke_check(
        include_feishu=not args.skip_feishu,
        include_bitable=not args.skip_bitable,
        include_openai=not args.skip_openai,
        include_llm=not args.skip_llm,
        include_deepseek=args.include_deepseek,
    )
    print(_report_json(report))
    if report.status == "failed":
        raise SystemExit(1)


def _run_feishu_checks(
    settings: Settings,
    client: httpx.Client | None,
    include_bitable: bool,
) -> list[ExternalSmokeCheck]:
    checks: list[ExternalSmokeCheck] = []
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        checks.append(
            _failed("feishu_token", "FEISHU_APP_ID and FEISHU_APP_SECRET are required.")
        )
        checks.append(_skipped("feishu_chat", "Skipped because Feishu token check failed."))
        if include_bitable:
            checks.append(_skipped("feishu_bitable", "Skipped because Feishu token check failed."))
        return checks

    auth = FeishuAuthProvider(settings=settings, client=client)
    try:
        auth.get_tenant_access_token()
    except Exception as exc:
        checks.append(_failed("feishu_token", f"Feishu tenant token check failed: {exc}"))
        checks.append(_skipped("feishu_chat", "Skipped because Feishu token check failed."))
        if include_bitable:
            checks.append(_skipped("feishu_bitable", "Skipped because Feishu token check failed."))
        return checks
    checks.append(_ok("feishu_token", "Feishu tenant_access_token fetched successfully."))

    checks.append(_run_feishu_chat_check(settings, auth, client))
    if include_bitable:
        checks.extend(_run_bitable_checks(settings, auth, client))
    else:
        checks.append(_skipped("feishu_bitable", "Bitable smoke checks skipped by CLI flag."))
    return checks


def _run_feishu_chat_check(
    settings: Settings,
    auth: FeishuAuthProvider,
    client: httpx.Client | None,
) -> ExternalSmokeCheck:
    if not settings.feishu_default_chat_id:
        return _failed("feishu_chat", "FEISHU_DEFAULT_CHAT_ID is required.")
    try:
        FeishuHttpGateway(auth_provider=auth, client=client).get_chat_info(
            settings.feishu_default_chat_id,
            idempotency_key="smoke-feishu-chat",
        )
    except Exception as exc:
        return _failed("feishu_chat", f"Feishu chat info check failed: {exc}")
    return _ok("feishu_chat", "Feishu chat info API is reachable for default chat.")


def _run_bitable_checks(
    settings: Settings,
    auth: FeishuAuthProvider,
    client: httpx.Client | None,
) -> list[ExternalSmokeCheck]:
    app_token = settings.feishu_bitable_app_token
    table_ids = _bitable_table_ids(settings)
    if not app_token or any(not table_id for table_id in table_ids.values()):
        return [
            _failed(
                "feishu_bitable",
                "FEISHU_BITABLE_APP_TOKEN and all FEISHU_BITABLE_*_TABLE_ID values are required.",
            )
        ]

    gateway = FeishuHttpBitableGateway(auth_provider=auth, client=client)
    checks: list[ExternalSmokeCheck] = []
    for name, table_id in table_ids.items():
        try:
            gateway.search_records(
                app_token,
                table_id or "",
                page_size=1,
                idempotency_key=f"smoke-bitable-{name}",
            )
        except Exception as exc:
            checks.append(_failed(f"feishu_bitable_{name}", f"Bitable search failed: {exc}"))
        else:
            checks.append(
                _ok(
                    f"feishu_bitable_{name}",
                    "Bitable records/search API is reachable without writing data.",
                )
            )
    return checks


def _run_openai_checks(
    settings: Settings,
    client: httpx.Client | None,
    include_llm: bool,
) -> list[ExternalSmokeCheck]:
    checks = [_run_embedding_check(settings, client)]
    if include_llm:
        checks.append(_run_llm_check(settings, client))
    else:
        checks.append(_skipped("openai_llm", "OpenAI LLM smoke call skipped by CLI flag."))
    return checks


def _run_embedding_check(settings: Settings, client: httpx.Client | None) -> ExternalSmokeCheck:
    api_key = settings.embedding_api_key_value()
    if not api_key or not settings.embedding_model:
        return _failed(
            "openai_embedding",
            "EMBEDDING_API_KEY/OPENAI_API_KEY and EMBEDDING_MODEL are required.",
        )
    try:
        vectors = OpenAIEmbeddingGateway(
            api_key=api_key.get_secret_value(),
            model=settings.embedding_model,
            client=client,
        ).embed_texts(["lietou external smoke"], purpose="external_smoke")
    except Exception as exc:
        return _failed("openai_embedding", f"OpenAI embedding check failed: {exc}")
    if not vectors or not vectors[0]:
        return _failed("openai_embedding", "OpenAI embedding response was empty.")
    return _ok("openai_embedding", "OpenAI embeddings API returned a vector.")


def _run_llm_check(settings: Settings, client: httpx.Client | None) -> ExternalSmokeCheck:
    if not settings.llm_api_key or not settings.llm_model:
        return _failed("openai_llm", "LLM_API_KEY and LLM_MODEL are required.")
    try:
        result = OpenAIResponsesLLMGateway(
            api_key=settings.llm_api_key.get_secret_value(),
            model=settings.llm_model,
            client=client,
        ).generate_structured(
            agent_name="ExternalSmokeAgent",
            context_pack=_smoke_context_pack(),
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
            },
            schema_name="external_smoke",
            max_output_tokens=64,
        )
    except Exception as exc:
        return _failed("openai_llm", f"OpenAI Responses check failed: {exc}")
    if not isinstance(result.get("ok"), bool):
        return _failed("openai_llm", "OpenAI Responses result did not match smoke schema.")
    return _ok("openai_llm", "OpenAI Responses API returned strict structured JSON.")


def _run_deepseek_check(settings: Settings, client: httpx.Client | None) -> ExternalSmokeCheck:
    if not settings.deepseek_api_key:
        return _skipped(
            "deepseek_llm",
            "DEEPSEEK_API_KEY is not set. User BYOK profiles are tested separately.",
        )
    try:
        result = DeepSeekChatCompletionsLLMGateway(
            api_key=settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            thinking=settings.deepseek_thinking,
            reasoning_effort=settings.deepseek_reasoning_effort,
            client=client,
        ).generate_structured(
            agent_name="ExternalSmokeAgent",
            context_pack=_smoke_context_pack(),
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
            },
            schema_name="external_smoke",
            max_output_tokens=64,
        )
    except Exception as exc:
        return _failed("deepseek_llm", f"DeepSeek chat JSON check failed: {exc}")
    if not isinstance(result.get("ok"), bool):
        return _failed("deepseek_llm", "DeepSeek JSON result did not match smoke schema.")
    return _ok("deepseek_llm", "DeepSeek chat API returned parseable JSON.")


def _smoke_context_pack() -> ContextPack:
    return ContextPack(
        thread_id="00000000-0000-4000-8000-000000000001",
        agent_name="ExternalSmokeAgent",
        task_brief="Return {'ok': true} for an external API smoke check.",
        node_goal="Verify OpenAI Responses structured output connectivity.",
        council_mode=CouncilMode.triage,
        mode_reason="runtime smoke check",
    )


def _bitable_table_ids(settings: Settings) -> dict[str, str | None]:
    return {
        "requisition": settings.feishu_bitable_requisition_table_id,
        "candidate": settings.feishu_bitable_candidate_table_id,
        "talent_map": settings.feishu_bitable_talent_map_table_id,
        "report": settings.feishu_bitable_report_table_id,
    }


def _ok(name: str, message: str) -> ExternalSmokeCheck:
    return ExternalSmokeCheck(name=name, status="ok", message=message)


def _failed(name: str, message: str) -> ExternalSmokeCheck:
    return ExternalSmokeCheck(name=name, status="failed", message=message)


def _skipped(name: str, message: str) -> ExternalSmokeCheck:
    return ExternalSmokeCheck(name=name, status="skipped", message=message)


def _report(checks: list[ExternalSmokeCheck]) -> ExternalSmokeReport:
    statuses = {check.status for check in checks}
    if "failed" in statuses:
        status: ReportStatus = "failed"
    elif "skipped" in statuses:
        status = "partial"
    else:
        status = "ok"
    return ExternalSmokeReport(status=status, checks=checks)


def _report_json(report: ExternalSmokeReport) -> str:
    return json.dumps(asdict(report), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
