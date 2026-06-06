from contextlib import contextmanager

from app.core.config import Settings, get_settings
from app.graphs.war_room import build_headhunter_war_room_graph
from app.policy.engine import PolicyEngine


class RuntimeGraphFactory:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        policy_engine: PolicyEngine | None = None,
        agent_harness=None,
        action_gate=None,
        action_executor=None,
    ):
        self.settings = settings or get_settings()
        self.policy_engine = policy_engine or PolicyEngine()
        self.agent_harness = agent_harness
        self.action_gate = action_gate
        self.action_executor = action_executor

    def create_headhunter_war_room_graph(self, *, checkpointer=None):
        return build_headhunter_war_room_graph(
            policy_engine=self.policy_engine,
            agent_harness=self.agent_harness,
            action_gate=self.action_gate,
            action_executor=self.action_executor,
            checkpointer=checkpointer,
        )

    @contextmanager
    def checkpointer(self):
        if not self.settings.checkpoint_db_url.startswith("postgresql+psycopg://"):
            raise RuntimeError("PostgreSQL checkpointer requires postgresql+psycopg:// URL")

        from langgraph.checkpoint.postgres import PostgresSaver

        with PostgresSaver.from_conn_string(
            _psycopg_conninfo(self.settings.checkpoint_db_url)
        ) as saver:
            saver.setup()
            yield saver

    @contextmanager
    def graph_with_postgres_checkpointer(self):
        with self.checkpointer() as saver:
            yield self.create_headhunter_war_room_graph(checkpointer=saver)


def _psycopg_conninfo(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)
