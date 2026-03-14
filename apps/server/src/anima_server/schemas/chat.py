from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    userId: int = Field(gt=0)
    stream: bool = False


class ChatResponse(BaseModel):
    response: str
    model: str
    provider: str
    toolsUsed: list[str] = Field(default_factory=list)


class ChatResetRequest(BaseModel):
    userId: int = Field(gt=0)


class ChatResetResponse(BaseModel):
    status: str


class ChatHistoryMessage(BaseModel):
    id: int
    userId: int
    role: str
    content: str
    model: str | None = None
    provider: str | None = None
    createdAt: datetime | None = None


class ChatHistoryClearResponse(BaseModel):
    status: str
