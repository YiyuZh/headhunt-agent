from uuid import uuid4

from app.runtime.war_room import WarRoomNotifier


class FakeOutboxWriter:
    def __init__(self):
        self.enqueued = []

    def enqueue_json(self, **kwargs):
        self.enqueued.append(kwargs)
        return "artifact://outbox/1"


def test_war_room_question_card_is_enqueued_not_sent_directly() -> None:
    writer = FakeOutboxWriter()
    notifier = WarRoomNotifier(outbox_writer=writer, default_chat_id="oc_default")

    payload_ref = notifier.enqueue_question_card(
        thread_id=uuid4(),
        chat_id=None,
        council_mode="triage",
        mode_reason="信息不足",
        questions=["请补充岗位目标"],
    )

    assert payload_ref == "artifact://outbox/1"
    assert writer.enqueued[0]["kind"] == "card_send"
    assert writer.enqueued[0]["payload"]["chat_id"] == "oc_default"
    assert "请补充岗位目标" in writer.enqueued[0]["payload"]["card"]["body"]["elements"][0]["text"][
        "content"
    ]
