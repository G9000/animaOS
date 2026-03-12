from __future__ import annotations

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
