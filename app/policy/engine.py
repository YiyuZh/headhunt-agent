from uuid import uuid4

from app.schemas.common import CouncilMode
from app.schemas.council import CouncilDeliberateRequest, TaskPlan

FULL_COUNCIL_TRIGGERS = ("三省六部", "完整会审", "全量会审", "full council")
HIGH_RISK_TERMS = ("外部触达", "推荐结论", "批量", "敏感", "联系方式")

TRIAGE_AGENTS = [
    "IntentRouterAgent",
    "ComplianceRiskAgent",
    "CouncilSynthesizerAgent",
]

LITE_AGENTS = [
    "IntentRouterAgent",
    "StrategyDraftAgent",
    "ComplianceRiskAgent",
    "CouncilSynthesizerAgent",
]

FULL_COUNCIL_AGENTS = [
    "CandidateJudgementAgent",
    "MarketCompAgent",
    "OutreachValueAgent",
    "SourcingMappingAgent",
    "ComplianceRiskAgent",
    "DataAutomationAgent",
    "ChallengeReviewAgent",
    "CouncilSynthesizerAgent",
]


class PolicyEngine:
    def create_task_plan(self, request: CouncilDeliberateRequest) -> TaskPlan:
        text = request.request_text
        user_forced_full_council = any(trigger in text for trigger in FULL_COUNCIL_TRIGGERS)

        if user_forced_full_council:
            mode = CouncilMode.full_council
            reason = "用户明确要求三省六部或完整会审"
            required_agents = FULL_COUNCIL_AGENTS
            optional_agents: list[str] = []
        elif len(text.strip()) < 20:
            mode = CouncilMode.triage
            reason = "输入信息不足，先快速分流和追问"
            required_agents = TRIAGE_AGENTS
            optional_agents = ["StrategyDraftAgent"]
        elif any(term in text for term in HIGH_RISK_TERMS):
            mode = CouncilMode.standard
            reason = "任务涉及较高风险动作，加入挑战和合规审查"
            required_agents = [
                "IntentRouterAgent",
                "StrategyDraftAgent",
                "ComplianceRiskAgent",
                "ChallengeReviewAgent",
                "CouncilSynthesizerAgent",
            ]
            optional_agents = ["DataAutomationAgent", "SourcingMappingAgent"]
        else:
            mode = CouncilMode.lite
            reason = "简单或常规任务，使用 lite 会审以降低 token 消耗"
            required_agents = LITE_AGENTS
            optional_agents = ["SourcingMappingAgent"]

        return TaskPlan(
            thread_id=request.thread_id or uuid4(),
            request_text=text,
            task_type=self._classify_task(text),
            council_mode=mode,
            mode_reason=reason,
            required_agents=required_agents,
            optional_agents=optional_agents,
            user_forced_full_council=user_forced_full_council,
            allowed_gateways=[
                "ArtifactStore",
                "MemoryGateway",
                "LLMGateway",
                "FeishuGateway",
            ],
        )

    @staticmethod
    def _classify_task(text: str) -> str:
        if "候选人" in text or "筛选" in text:
            return "candidate_screening"
        if "人才地图" in text or "Mapping" in text or "mapping" in text:
            return "talent_mapping"
        if "报告" in text or "话术" in text:
            return "report_draft"
        if "复盘" in text or "记忆" in text:
            return "review"
        return "requisition_calibration"
