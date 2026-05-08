from dataclasses import dataclass, field
from enum import Enum, auto
from anthropic.types import MessageParam

class AgentStatus(Enum):
    RUNNING = auto()
    DONE    = auto()
    FAILED  = auto()
    LIMIT   = auto()

@dataclass
class AgentState:
    task: str
    history: list[MessageParam] = field(default_factory=list)
    iteration: int = 0
    status: AgentStatus = AgentStatus.RUNNING
    last_action: str | None = None
    last_observation: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status != AgentStatus.RUNNING