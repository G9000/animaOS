from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.db import get_db
from anima_server.models.task import Task
from anima_server.schemas.task import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from anima_server.services.corefs.formats import TaskDocument
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.task_authority import (
    TaskAuthorityError,
    list_canonical_tasks,
    task_corefs_authority_active,
)
from anima_server.services.corefs.task_mutations import (
    TaskMutationError,
    create_canonical_task,
    delete_canonical_task,
    update_canonical_task,
)
from anima_server.services.corefs.writing_source import prepare_writing_source_catalog

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


def _task_to_response(task: Task) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        userId=task.user_id,
        text=task.text,
        done=task.done,
        priority=task.priority,
        dueDate=task.due_date,
        completedAt=task.completed_at,
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


def _canonical_task_to_response(task: TaskDocument, *, user_id: int) -> TaskResponse:
    return TaskResponse(
        id=task.legacy_id,
        userId=user_id,
        text=task.text,
        done=task.done,
        priority=task.priority,
        dueDate=task.due_date,
        completedAt=task.completed_at,
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


def _raise_task_mutation_error(exc: Exception) -> Never:
    code = str(exc)
    if code == "corefs_mutation_not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc
    status_code = (
        status.HTTP_503_SERVICE_UNAVAILABLE
        if code
        in {
            "corefs_native_mutation_unavailable",
            "corefs_native_mutation_result_invalid",
            "Native CoreFS task mutation result is invalid.",
        }
        else status.HTTP_409_CONFLICT
    )
    raise HTTPException(
        status_code=status_code,
        detail={"code": code or "corefs_task_mutation_failed"},
    ) from exc


def _refresh_task_shadow(*, session: object, db: Session) -> None:
    try:
        prepare_writing_source_catalog(session=session, db=db)
    except Exception as exc:
        logger.exception("Encrypted task shadow validation failed")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "corefs_task_shadow_validation_failed",
                "message": "The legacy task was saved, but its encrypted shadow needs retry.",
            },
        ) from exc


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> list[TaskResponse]:
    session = await require_unlocked_session_async(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

    if task_corefs_authority_active(session):
        try:
            return [
                _canonical_task_to_response(task, user_id=session.user_id)
                for task in list_canonical_tasks(session=session)
            ]
        except TaskAuthorityError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.user_id == userId)
            .order_by(Task.done, Task.priority.desc(), Task.created_at.desc())
        ).all()
    )
    return [_task_to_response(t) for t in tasks]


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TaskResponse:
    session = await require_unlocked_session_async(request)
    if session.user_id != payload.userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")
    if task_corefs_authority_active(session):
        try:
            task = create_canonical_task(
                session=session,
                text=payload.text,
                priority=payload.priority,
                due_date=payload.dueDate,
            )
        except (CoreFsMutationUnavailable, TaskAuthorityError, TaskMutationError, ValueError) as exc:
            _raise_task_mutation_error(exc)
        return _canonical_task_to_response(task, user_id=session.user_id)

    task = Task(
        user_id=payload.userId,
        text=payload.text,
        priority=payload.priority,
        due_date=payload.dueDate,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    _refresh_task_shadow(session=session, db=db)
    return _task_to_response(task)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    payload: TaskUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TaskResponse:
    session = await require_unlocked_session_async(request)
    if task_corefs_authority_active(session):
        try:
            task = update_canonical_task(
                session=session,
                legacy_id=task_id,
                text=payload.text,
                done=payload.done,
                priority=payload.priority,
                due_date=payload.dueDate,
                due_date_present="dueDate" in payload.model_fields_set,
            )
        except (CoreFsMutationUnavailable, TaskAuthorityError, TaskMutationError, ValueError) as exc:
            _raise_task_mutation_error(exc)
        if task is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        return _canonical_task_to_response(task, user_id=session.user_id)

    task = db.get(Task, task_id)
    if task is None or task.user_id != session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    if payload.text is not None:
        task.text = payload.text
    if payload.priority is not None:
        task.priority = payload.priority
    if payload.dueDate is not None:
        task.due_date = payload.dueDate if payload.dueDate else None
    if payload.done is not None:
        task.done = payload.done
        task.completed_at = datetime.now(UTC) if payload.done else None

    task.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(task)
    _refresh_task_shadow(session=session, db=db)
    return _task_to_response(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    session = await require_unlocked_session_async(request)
    if task_corefs_authority_active(session):
        try:
            deleted = delete_canonical_task(session=session, legacy_id=task_id)
        except (CoreFsMutationUnavailable, TaskAuthorityError, TaskMutationError, ValueError) as exc:
            _raise_task_mutation_error(exc)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
        return {"status": "deleted"}

    task = db.get(Task, task_id)
    if task is None or task.user_id != session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    db.delete(task)
    db.commit()
    _refresh_task_shadow(session=session, db=db)
    return {"status": "deleted"}
