from uuid import UUID, uuid4

import pytest

from app.feishu.cards import build_task_confirmation_card
from app.feishu.task_intake import (
    TaskIntakeSchemaError,
    build_graph_dispatch_payload,
    create_task_plan,
    parse_task_intake,
    parse_task_intake_with_llm,
)
from app.gateways.llm import LLMGatewayError
from app.policy.engine import PolicyEngine


def test_parse_task_intake_extracts_feishu_message_scope_and_text() -> None:
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1", "user_id": "u_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"新建岗位：北京 AI 产品经理，生成岗位校准和人才地图"}',
            },
        }
    }

    intake = parse_task_intake(payload, tenant_key="tenant_1")

    assert intake.request_text == "新建岗位：北京 AI 产品经理，生成岗位校准和人才地图"
    assert intake.chat_id == "oc_1"
    assert intake.model_owner_user_id == "ou_1"
    assert intake.model_owner_id_type == "open_id"
    assert intake.model_guild_id == "oc_1"
    assert intake.source_ref == "feishu://message/tenant_1/oc_1/om_1"
    assert isinstance(intake.thread_id, UUID)
    assert intake.field_sources[0]["field"] == "request_text"


def test_task_confirmation_card_contains_approve_and_reject_values() -> None:
    thread_id = uuid4()
    task_id = uuid4()

    card = build_task_confirmation_card(
        thread_id=thread_id,
        task_id=task_id,
        task_payload_ref="artifact://task",
        source_ref="feishu://message/tenant/oc/om",
        request_text="新建岗位：AI 产品经理",
        task_type="requisition_calibration",
        council_mode="lite",
        mode_reason="常规任务",
        field_sources=[{"field": "request_text", "source": "message", "confidence": 1.0}],
        missing_fields=[],
        assumptions=[],
    )

    approve, reject = card["body"]["elements"][1:]
    assert approve["behaviors"][0]["value"]["action_kind"] == "task_double_check"
    assert approve["behaviors"][0]["value"]["decision"] == "approve"
    assert reject["behaviors"][0]["value"]["decision"] == "reject"
    assert approve["behaviors"][0]["value"]["idempotency_key"].startswith("task_confirm:")


def test_task_intake_llm_parser_structures_confirmation_card() -> None:
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"新建岗位：北京 AI 产品经理，生成岗位校准和人才地图"}',
            },
        }
    }
    intake = parse_task_intake(payload, tenant_key="tenant_1")

    parsed = parse_task_intake_with_llm(
        intake,
        FakeTaskIntakeLLM(),
        model_profile_id=uuid4(),
    )
    task_plan = create_task_plan(parsed, PolicyEngine())
    card = build_task_confirmation_card(
        thread_id=parsed.thread_id,
        task_id=parsed.task_id,
        task_payload_ref="artifact://task",
        source_ref=parsed.source_ref,
        request_text=task_plan.request_text,
        task_type=task_plan.task_type,
        council_mode=task_plan.council_mode.value,
        mode_reason=task_plan.mode_reason,
        field_sources=parsed.field_sources,
        missing_fields=parsed.missing_fields,
        assumptions=parsed.assumptions,
        structured_fields=parsed.structured_fields,
        raw_request_text=parsed.request_text,
        parser_status=parsed.parser_status,
        parser_error=parsed.parser_error,
    )

    content = card["body"]["elements"][0]["text"]["content"]
    assert parsed.parser_status == "llm_parsed"
    assert "- 岗位: AI 产品经理" in content
    assert "- 地点: 北京" in content
    assert "- 交付物: 岗位校准、人才地图" in content
    assert "大模型已完成结构化解析" in content
    assert "新建岗位：北京 AI 产品经理" in content


def test_task_intake_llm_parser_rejects_empty_json_object() -> None:
    intake = parse_task_intake(_message_payload(), tenant_key="tenant_1")
    llm = FakeTaskIntakeLLM({})

    with pytest.raises(TaskIntakeSchemaError, match="missing required fields"):
        parse_task_intake_with_llm(
            intake,
            llm,
            model_profile_id=uuid4(),
        )
    assert llm.calls == 2


def test_task_intake_parser_uses_bracketed_rules_after_llm_json_failure() -> None:
    intake = parse_task_intake(_bracketed_message_payload(), tenant_key="tenant_1")
    llm = FakeTaskIntakeLLM(
        LLMGatewayError("LLM structured output is not valid JSON"),
    )

    parsed = parse_task_intake_with_llm(
        intake,
        llm,
        model_profile_id=uuid4(),
    )
    task_plan = create_task_plan(parsed, PolicyEngine())

    assert parsed.parser_status == "rule_parsed_after_llm_failed"
    assert parsed.structured_fields["task"] == "新建岗位，做岗位校准和人才地图"
    assert parsed.structured_fields["project"] == "测试项目-北京 AI 产品经理"
    assert parsed.structured_fields["role"] == "AI 产品经理"
    assert parsed.structured_fields["location"] == "北京"
    assert parsed.structured_fields["must_have"] == [
        "AI 产品经验",
        "B 端产品经验",
        "能和算法/工程协作",
    ]
    assert parsed.structured_fields["deliverables"] == [
        "岗位校准",
        "人才地图方向",
        "候选人筛选标准",
        "需要追问客户的问题",
    ]
    assert task_plan.task_type == "talent_mapping"


def test_task_intake_parser_does_not_rule_parse_unlabeled_text_after_llm_json_failure() -> None:
    intake = parse_task_intake(_message_payload(), tenant_key="tenant_1")

    with pytest.raises(LLMGatewayError, match="not valid JSON"):
        parse_task_intake_with_llm(
            intake,
            FakeTaskIntakeLLM(LLMGatewayError("LLM structured output is not valid JSON")),
            model_profile_id=uuid4(),
        )


def test_task_intake_llm_parser_retries_schema_error_once_then_succeeds() -> None:
    intake = parse_task_intake(_message_payload(), tenant_key="tenant_1")
    llm = FakeTaskIntakeLLM([{}, None])

    parsed = parse_task_intake_with_llm(
        intake,
        llm,
        model_profile_id=uuid4(),
    )

    assert llm.calls == 2
    assert parsed.parser_status == "llm_parsed"
    assert parsed.structured_fields["role"] == "AI 产品经理"


def test_task_intake_llm_parser_rejects_schema_shaped_but_empty_task() -> None:
    intake = parse_task_intake(_message_payload(), tenant_key="tenant_1")
    empty_structured_task = {
        "task": "",
        "project": "",
        "role": "",
        "location": "",
        "level_years": "",
        "compensation": "",
        "job_description": "",
        "must_have": [],
        "nice_to_have": [],
        "target_companies": [],
        "excluded_companies": [],
        "deliverables": [],
        "constraints": [],
        "missing_fields": ["岗位信息不足"],
        "assumptions": [],
        "confidence": 0.2,
    }

    with pytest.raises(TaskIntakeSchemaError, match="no usable recruiting fields"):
        parse_task_intake_with_llm(
            intake,
            FakeTaskIntakeLLM(empty_structured_task),
            model_profile_id=uuid4(),
        )


def test_failed_parser_status_line_does_not_claim_rule_fallback() -> None:
    card = build_task_confirmation_card(
        thread_id=uuid4(),
        task_id=uuid4(),
        task_payload_ref="artifact://task",
        source_ref="feishu://message/tenant/oc/om",
        request_text="新建岗位：AI 产品经理",
        task_type="requisition_calibration",
        council_mode="lite",
        mode_reason="常规任务",
        field_sources=[],
        missing_fields=[],
        assumptions=[],
        structured_fields={},
        raw_request_text="新建岗位：AI 产品经理",
        parser_status="llm_failed",
        parser_error="LLM structured output is not valid JSON",
    )

    content = card["body"]["elements"][0]["text"]["content"]
    assert "未启动任务" in content
    assert "已回退规则解析" not in content


def test_graph_dispatch_payload_preserves_byok_scope_after_double_check() -> None:
    payload = {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"筛选候选人：目标岗位是 AI 平台负责人"}',
            },
        }
    }
    intake = parse_task_intake(payload, tenant_key="tenant_1")
    task_plan = create_task_plan(intake, PolicyEngine())
    model_profile_id = uuid4()

    graph_payload = build_graph_dispatch_payload(
        intake=intake,
        task_plan=task_plan,
        model_profile_id=model_profile_id,
    )

    assert graph_payload["source"] == "feishu"
    assert graph_payload["thread_id"] == str(intake.thread_id)
    assert graph_payload["task_id"] == str(intake.task_id)
    assert graph_payload["model_profile_id"] == str(model_profile_id)
    assert graph_payload["model_owner_user_id"] == "ou_1"
    assert graph_payload["model_owner_id_type"] == "open_id"
    assert graph_payload["model_guild_id"] == "oc_1"
    assert graph_payload["authorization"]["status"] == "pending_feishu_double_check"


class FakeTaskIntakeLLM:
    def __init__(self, result: dict | list[dict | None] | Exception | None = None):
        self.result = result
        self.calls = 0

    def generate_structured(self, **kwargs):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        if isinstance(self.result, list):
            result = self.result.pop(0)
            if isinstance(result, Exception):
                raise result
            if result is not None:
                return result
        elif self.result is not None:
            return self.result
        return {
            "task": "新建岗位",
            "project": "北京 AI 产品经理",
            "role": "AI 产品经理",
            "location": "北京",
            "level_years": "",
            "compensation": "",
            "job_description": "",
            "must_have": [],
            "nice_to_have": [],
            "target_companies": [],
            "excluded_companies": [],
            "deliverables": ["岗位校准", "人才地图"],
            "constraints": [],
            "missing_fields": [],
            "assumptions": [],
            "confidence": 0.9,
        }


def _message_payload() -> dict:
    return {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": '{"text":"新建岗位：北京 AI 产品经理，生成岗位校准和人才地图"}',
            },
        }
    }


def _bracketed_message_payload() -> dict:
    text = (
        "@_user_1 @实习生开发猎头agent\n"
        "【任务】新建岗位，做岗位校准和人才地图\n"
        "【项目】测试项目-北京 AI 产品经理\n"
        "【岗位】AI 产品经理\n"
        "【地点】北京\n"
        "【职级/年限】5-8 年，P6/P7\n"
        "【薪资】40-70K\n"
        "【JD】负责 AI 产品规划、需求拆解、模型能力落地、跨团队推进\n"
        "【Must-have】AI 产品经验、B 端产品经验、能和算法/工程协作\n"
        "【Nice-to-have】大模型应用、智能客服/知识库/Agent 产品经验\n"
        "【目标公司】字节、百度、阿里、腾讯、美团、快手\n"
        "【排除公司】暂不排除\n"
        "【交付物】岗位校准、人才地图方向、候选人筛选标准、需要追问客户的问题\n"
        "【限制】不要自动外部触达，不要自动写入业务表，"
        "所有业务动作先给我确认 请走三省六部完整会审。"
    )
    return {
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": json_payload(text),
            },
        }
    }


def json_payload(text: str) -> str:
    import json

    return json.dumps({"text": text}, ensure_ascii=False)
