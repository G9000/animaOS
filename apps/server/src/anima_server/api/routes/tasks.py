from __future__ import annotations

import logging
from typing import Never

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.db import get_db
from anima_server.schemas.task import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from anima_server.services.corefs.formats import TaskDocument
from anima_server.services.corefs.logical import CoreFsMutationUnavailable
from anima_server.services.corefs.task_authority import (
    TaskAuthorityError,
    list_canonical_tasks,
)
from anima_server.services.corefs.task_mutations import (
    TaskMutationError,
    create_canonical_task,
    delete_canonical_task,
    update_canonical_task,
)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)


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
        status_code=status_code, detail={"code": code or "corefs_task_mutation_failed"}
    ) from exc


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    request: Request, userId: int = Query(ge=0), db: Session = Depends(get_db)
) -> list[TaskResponse]:
    session = await require_unlocked_session_async(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")
    try:
        return [
            _canonical_task_to_response(task, user_id=session.user_id)
            for task in list_canonical_tasks(session=session)
        ]
    except TaskAuthorityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest, request: Request, db: Session = Depends(get_db)
) -> TaskResponse:
    session = await require_unlocked_session_async(request)
    if session.user_id != payload.userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")
    try:
        task = create_canonical_task(
            session=session, text=payload.text, priority=payload.priority, due_date=payload.dueDate
        )
    except (CoreFsMutationUnavailable, TaskAuthorityError, TaskMutationError, ValueError) as exc:
        _raise_task_mutation_error(exc)
    return _canonical_task_to_response(task, user_id=session.user_id)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int, payload: TaskUpdateRequest, request: Request, db: Session = Depends(get_db)
) -> TaskResponse:
    session = await require_unlocked_session_async(request)
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


@router.delete("/{task_id}")
async def delete_task(
    task_id: int, request: Request, db: Session = Depends(get_db)
) -> dict[str, str]:
    session = await require_unlocked_session_async(request)
    try:
        deleted = delete_canonical_task(session=session, legacy_id=task_id)
    except (CoreFsMutationUnavailable, TaskAuthorityError, TaskMutationError, ValueError) as exc:
        _raise_task_mutation_error(exc)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return {"status": "deleted"}
