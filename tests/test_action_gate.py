from uuid import uuid4

from sqlalchemy.sql.selectable import Select

from app.runtime.action_gate import ActionGate
from app.schemas.artifacts import ArtifactRef


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self):
        self.statements = []
        self.flush_count = 0
        self.existing_action = None
        self.select_results = []

    def execute(self, statement):
        self.statements.append(statement)
        if isinstance(statement, Select) and self.select_results:
            return FakeResult(self.select_results.pop(0))
        return FakeResult(self.existing_action)

    def get(self, model, key):
        return None

    def flush(self):
        self.flush_count += 1


class FakeWarRoomNotifier:
    def __init__(self):
        self.approval_cards = []

    def enqueue_approval_card(self, **kwargs):
        self.approval_cards.append(kwargs)
        return "artifact://outbox/card"


def test_action_gate_persists_action_proposal_and_enqueues_confirmation_card() -> None:
    fake = FakeSession()
    war_room = FakeWarRoomNotifier()
    artifact = ArtifactRef(
        artifact_id=uuid4(),
        kind="CandidateMatchDraft",
        summary="候选人推荐草稿",
        content_ref="artifact://candidate/1",
    )

    proposed = ActionGate(fake, war_room_notifier=war_room).propose_action(
        thread_id=uuid4(),
        action_type="recommendation_commit",
        payload_summary="保存推荐结论",
        payload={"candidate_id": "c_1"},
        idempotency_key="idem-action-1",
        artifact_refs=[artifact],
        chat_id="oc_1",
        council_mode="standard",
        mode_reason="推荐结论需要人工确认",
    )

    assert proposed.payload_ref.startswith("artifact://action-proposal/")
    assert proposed.idempotency_key == "idem-action-1"
    assert len(fake.statements) == 5
    assert fake.flush_count == 1
    assert war_room.approval_cards[0]["action_id"] == proposed.action_id
    assert war_room.approval_cards[0]["interrupt_id"] == proposed.interrupt_id
    assert war_room.approval_cards[0]["chat_id"] == "oc_1"
    assert war_room.approval_cards[0]["payload_ref"] == proposed.payload_ref


def test_action_gate_reuses_existing_action_for_same_idempotency_key() -> None:
    fake = FakeSession()
    action_id = uuid4()
    interrupt_id = uuid4()
    fake.existing_action = type(
        "ExistingAction",
        (),
        {
            "id": action_id,
            "interrupt_id": interrupt_id,
            "payload_ref": "artifact://existing",
            "idempotency_key": "idem-action-1",
        },
    )()
    war_room = FakeWarRoomNotifier()

    proposed = ActionGate(fake, war_room_notifier=war_room).propose_action(
        thread_id=uuid4(),
        action_type="recommendation_commit",
        payload_summary="保存推荐结论",
        payload={"candidate_id": "c_1"},
        idempotency_key="idem-action-1",
        artifact_refs=[],
        chat_id="oc_1",
    )

    assert proposed.action_id == action_id
    assert proposed.interrupt_id == interrupt_id
    assert proposed.payload_ref == "artifact://existing"
    assert len(fake.statements) == 1
    assert fake.flush_count == 0
    assert war_room.approval_cards == []


def test_action_gate_reuses_conflicting_action_after_insert_race() -> None:
    fake = FakeSession()
    action_id = uuid4()
    interrupt_id = uuid4()
    existing = type(
        "ExistingAction",
        (),
        {
            "id": action_id,
            "interrupt_id": interrupt_id,
            "payload_ref": "artifact://existing",
            "idempotency_key": "idem-action-race",
        },
    )()
    fake.select_results = [None, existing]
    war_room = FakeWarRoomNotifier()

    proposed = ActionGate(fake, war_room_notifier=war_room).propose_action(
        thread_id=uuid4(),
        action_type="recommendation_commit",
        payload_summary="保存推荐结论",
        payload={"candidate_id": "c_1"},
        idempotency_key="idem-action-race",
        artifact_refs=[],
        chat_id="oc_1",
    )

    assert proposed.action_id == action_id
    assert proposed.interrupt_id == interrupt_id
    assert proposed.payload_ref == "artifact://existing"
    assert fake.flush_count == 0
    assert war_room.approval_cards == []
