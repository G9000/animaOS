from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session_async
from anima_server.db import get_db
from anima_server.models.task import Task
from anima_server.schemas.task import TaskCreateRequest, TaskResponse, TaskUpdateRequest

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


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


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    request: Request,
    userId: int = Query(ge=0),
    db: Session = Depends(get_db),
) -> list[TaskResponse]:
    session = await require_unlocked_session_async(request)
    if session.user_id != userId:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session user mismatch.")

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

    task = Task(
        user_id=payload.userId,
        text=payload.text,
        priority=payload.priority,
        due_date=payload.dueDate,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return _task_to_response(task)


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    payload: TaskUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> TaskResponse:
    session = await require_unlocked_session_async(request)

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
    return _task_to_response(task)


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    session = await require_unlocked_session_async(request)

    task = db.get(Task, task_id)
    if task is None or task.user_id != session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")

    db.delete(task)
    db.commit()
    return {"status": "deleted"}
