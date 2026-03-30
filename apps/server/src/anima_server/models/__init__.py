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
    MemoryEpisode,
    MemoryItem,
    MemoryItemTag,
    MemoryVector,
)
from anima_server.models.consciousness import (
    AgentProfile,
    EmotionalSignal,
    SelfModelBlock,
)
from anima_server.models.links import DiscordLink, TelegramLink
from anima_server.models.pending_memory_op import PendingMemoryOp
from anima_server.models.runtime import (
    RuntimeBackgroundTaskRun,
    RuntimeMessage,
    RuntimeRun,
    RuntimeStep,
    RuntimeThread,
)
from anima_server.models.runtime_consciousness import (
    ActiveIntention,
    CurrentEmotion,
    WorkingContext,
)
from anima_server.models.runtime_embedding import RuntimeEmbedding
from anima_server.models.soul_consciousness import (
    CoreEmotionalPattern,
    GrowthLogEntry,
    IdentityBlock,
)
from anima_server.models.task import Task
from anima_server.models.user import User
from anima_server.models.user_key import UserKey

__all__ = [
    "ActiveIntention",
    "AgentMessage",
    "AgentProfile",
    "AgentRun",
    "AgentStep",
    "AgentThread",
    "BackgroundTaskRun",
    "Base",
    "CoreEmotionalPattern",
    "CurrentEmotion",
    "DiscordLink",
    "EmotionalSignal",
    "ForgetAuditLog",
    "GrowthLogEntry",
    "IdentityBlock",
    "KGEntity",
    "KGRelation",
    "MemoryClaim",
    "MemoryClaimEvidence",
    "MemoryEpisode",
    "MemoryItem",
    "MemoryItemTag",
    "MemoryVector",
    "PendingMemoryOp",
    "RuntimeBackgroundTaskRun",
    "RuntimeEmbedding",
    "RuntimeMessage",
    "RuntimeRun",
    "RuntimeStep",
    "RuntimeThread",
    "SelfModelBlock",
    "Task",
    "TelegramLink",
    "User",
    "UserKey",
    "WorkingContext",
]
