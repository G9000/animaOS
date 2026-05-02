from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from anima_server.models import (
    AgentMessage,
    AgentRun,
    AgentStep,
    AgentThread,
    BackgroundTaskRun,
    CoreEmotionalPattern,
    EmotionalSignal,
    ForgetAuditLog,
    GrowthLogEntry,
    IdentityBlock,
    KGEntity,
    KGRelation,
    MemoryClaim,
    MemoryClaimEvidence,
    MemoryEpisode,
    MemoryItem,
    MemoryItemTag,
    MemoryVector,
    RuntimeBackgroundTaskRun,
    RuntimeEmbedding,
    RuntimeMessage,
    RuntimeRun,
    RuntimeStep,
    RuntimeThread,
    SelfModelBlock,
    Task,
)
from anima_server.models.pending_memory_op import PendingMemoryOp
from anima_server.models.runtime_consciousness import (
    ActiveIntention,
    CurrentEmotion,
    WorkingContext,
)
from anima_server.models.runtime_memory import (
    MemoryAccessLog,
    MemoryCandidate,
    MemoryRetrievalFeedback,
    PromotionJournal,
    RuntimeSessionNote,
)


def reset_eval_user_state(
    *,
    user_id: int,
    soul_db: Session,
    runtime_db: Session,
) -> dict[str, int]:
    """Delete benchmark-generated state for one user.

    This intentionally preserves the account row and agent profile so a
    disposable eval account can be reused across benchmark cases.
    """

    deleted: dict[str, int] = {}

    _reset_runtime_state(runtime_db, user_id=user_id, deleted=deleted)
    _reset_soul_state(soul_db, user_id=user_id, deleted=deleted)

    runtime_db.commit()
    soul_db.commit()
    return deleted


def _reset_runtime_state(
    db: Session,
    *,
    user_id: int,
    deleted: dict[str, int],
) -> None:
    thread_ids = select(RuntimeThread.id).where(RuntimeThread.user_id == user_id)

    _delete(db, deleted, "runtime_session_notes", delete(RuntimeSessionNote).where(RuntimeSessionNote.user_id == user_id))
    _delete(db, deleted, "current_emotions", delete(CurrentEmotion).where(CurrentEmotion.user_id == user_id))
    _delete(db, deleted, "working_context", delete(WorkingContext).where(WorkingContext.user_id == user_id))
    _delete(db, deleted, "active_intentions", delete(ActiveIntention).where(ActiveIntention.user_id == user_id))
    _delete(db, deleted, "runtime_steps", delete(RuntimeStep).where(RuntimeStep.thread_id.in_(thread_ids)))
    _delete(db, deleted, "runtime_messages", delete(RuntimeMessage).where(RuntimeMessage.user_id == user_id))
    _delete(db, deleted, "runtime_runs", delete(RuntimeRun).where(RuntimeRun.user_id == user_id))
    _delete(db, deleted, "runtime_threads", delete(RuntimeThread).where(RuntimeThread.user_id == user_id))
    _delete(db, deleted, "pending_memory_ops", delete(PendingMemoryOp).where(PendingMemoryOp.user_id == user_id))
    _delete(db, deleted, "memory_candidates", delete(MemoryCandidate).where(MemoryCandidate.user_id == user_id))
    _delete(db, deleted, "promotion_journal", delete(PromotionJournal).where(PromotionJournal.user_id == user_id))
    _delete(db, deleted, "memory_access_log", delete(MemoryAccessLog).where(MemoryAccessLog.user_id == user_id))
    _delete(
        db,
        deleted,
        "memory_retrieval_feedback",
        delete(MemoryRetrievalFeedback).where(MemoryRetrievalFeedback.user_id == user_id),
    )
    _delete(db, deleted, "runtime_embeddings", delete(RuntimeEmbedding).where(RuntimeEmbedding.user_id == user_id))
    _delete(
        db,
        deleted,
        "runtime_background_task_runs",
        delete(RuntimeBackgroundTaskRun).where(RuntimeBackgroundTaskRun.user_id == user_id),
    )


def _reset_soul_state(
    db: Session,
    *,
    user_id: int,
    deleted: dict[str, int],
) -> None:
    agent_thread_ids = select(AgentThread.id).where(AgentThread.user_id == user_id)
    memory_item_ids = select(MemoryItem.id).where(MemoryItem.user_id == user_id)
    memory_claim_ids = select(MemoryClaim.id).where(MemoryClaim.user_id == user_id)

    _delete(db, deleted, "agent_steps", delete(AgentStep).where(AgentStep.thread_id.in_(agent_thread_ids)))
    _delete(db, deleted, "agent_messages", delete(AgentMessage).where(AgentMessage.thread_id.in_(agent_thread_ids)))
    _delete(db, deleted, "agent_runs", delete(AgentRun).where(AgentRun.user_id == user_id))
    _delete(db, deleted, "agent_threads", delete(AgentThread).where(AgentThread.user_id == user_id))

    _delete(
        db,
        deleted,
        "memory_claim_evidence",
        delete(MemoryClaimEvidence).where(MemoryClaimEvidence.claim_id.in_(memory_claim_ids)),
    )
    _delete(db, deleted, "kg_relations", delete(KGRelation).where(KGRelation.user_id == user_id))
    _delete(db, deleted, "kg_entities", delete(KGEntity).where(KGEntity.user_id == user_id))
    _delete(db, deleted, "memory_vectors", delete(MemoryVector).where(MemoryVector.user_id == user_id))
    _delete(db, deleted, "memory_item_tags", delete(MemoryItemTag).where(MemoryItemTag.user_id == user_id))
    _delete(db, deleted, "memory_claims", delete(MemoryClaim).where(MemoryClaim.user_id == user_id))
    _update(
        db,
        deleted,
        "memory_items_superseded_refs",
        update(MemoryItem)
        .where(MemoryItem.superseded_by.in_(memory_item_ids))
        .values(superseded_by=None),
    )
    _delete(db, deleted, "memory_episodes", delete(MemoryEpisode).where(MemoryEpisode.user_id == user_id))
    _delete(db, deleted, "memory_items", delete(MemoryItem).where(MemoryItem.user_id == user_id))

    _delete(db, deleted, "tasks", delete(Task).where(Task.user_id == user_id))
    _delete(db, deleted, "emotional_signals", delete(EmotionalSignal).where(EmotionalSignal.user_id == user_id))
    _delete(db, deleted, "self_model_blocks", delete(SelfModelBlock).where(SelfModelBlock.user_id == user_id))
    _delete(db, deleted, "identity_blocks", delete(IdentityBlock).where(IdentityBlock.user_id == user_id))
    _delete(db, deleted, "growth_log", delete(GrowthLogEntry).where(GrowthLogEntry.user_id == user_id))
    _delete(
        db,
        deleted,
        "core_emotional_patterns",
        delete(CoreEmotionalPattern).where(CoreEmotionalPattern.user_id == user_id),
    )
    _delete(
        db,
        deleted,
        "background_task_runs",
        delete(BackgroundTaskRun).where(BackgroundTaskRun.user_id == user_id),
    )
    _delete(db, deleted, "forget_audit_log", delete(ForgetAuditLog).where(ForgetAuditLog.user_id == user_id))


def _delete(
    db: Session,
    deleted: dict[str, int],
    key: str,
    statement,
) -> None:
    result = db.execute(statement.execution_options(synchronize_session=False))
    deleted[key] = _rowcount(result.rowcount)


def _update(
    db: Session,
    deleted: dict[str, int],
    key: str,
    statement,
) -> None:
    result = db.execute(statement.execution_options(synchronize_session=False))
    deleted[key] = _rowcount(result.rowcount)


def _rowcount(rowcount: int | None) -> int:
    return int(rowcount or 0)
