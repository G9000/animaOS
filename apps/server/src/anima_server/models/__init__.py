"""Import SQLAlchemy models here so Alembic can discover metadata."""

from anima_server.db.base import Base
from anima_server.models.agent_runtime import (
    AgentMessage,
    AgentRun,
    AgentStep,
    AgentThread,
    BackgroundTaskRun,
    ForgetAuditLog,
    KGEntity,
    KGRelation,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryDailyLog,
    MemoryEpisode,
    MemoryItem,
    MemoryItemTag,
    MemoryVector,
    SessionNote,
)
from anima_server.models.consciousness import (
    AgentProfile,
    EmotionalSignal,
    SelfModelBlock,
)
from anima_server.models.links import DiscordLink, TelegramLink
from anima_server.models.runtime import (
    RuntimeBackgroundTaskRun,
    RuntimeMessage,
    RuntimeRun,
    RuntimeStep,
    RuntimeThread,
)
from anima_server.models.task import Task
from anima_server.models.user import User
from anima_server.models.user_key import UserKey

__all__ = [
    "AgentMessage",
    "AgentProfile",
    "AgentRun",
    "AgentStep",
    "AgentThread",
    "BackgroundTaskRun",
    "Base",
    "DiscordLink",
    "EmotionalSignal",
    "ForgetAuditLog",
    "KGEntity",
    "KGRelation",
    "MemoryClaim",
    "MemoryClaimEvidence",
    "MemoryDailyLog",
    "MemoryEpisode",
    "MemoryItem",
    "MemoryItemTag",
    "MemoryVector",
    "RuntimeBackgroundTaskRun",
    "RuntimeMessage",
    "RuntimeRun",
    "RuntimeStep",
    "RuntimeThread",
    "SelfModelBlock",
    "SessionNote",
    "Task",
    "TelegramLink",
    "User",
    "UserKey",
]
