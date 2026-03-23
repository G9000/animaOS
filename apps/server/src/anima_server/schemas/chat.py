from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    userId: int = Field(ge=0)
    stream: bool = False
    source: str | None = None


class ChatResponse(BaseModel):
    response: str
    model: str
    provider: str
    toolsUsed: list[str] = Field(default_factory=list)


class ChatResetRequest(BaseModel):
    userId: int = Field(ge=0)


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
    source: str | None = None


class ChatHistoryClearResponse(BaseModel):
    status: str


class CancelRunRequest(BaseModel):
    userId: int = Field(ge=0)


class CancelRunResponse(BaseModel):
    runId: int
    status: str


class DryRunRequest(BaseModel):
    message: str = Field(min_length=1)
    userId: int = Field(ge=0)


class DryRunResponse(BaseModel):
    systemPrompt: str
    messages: list[dict] = Field(default_factory=list)
    allowedTools: list[str]
    estimatedPromptTokens: int
    toolSchemas: list[dict]
    memoryBlockCount: int


class ApprovalRequest(BaseModel):
    userId: int = Field(ge=0)
    approved: bool
    reason: str | None = None
    stream: bool = False


class ApprovalResponse(BaseModel):
    runId: int
    status: str
    response: str = ""
    model: str = ""
    provider: str = ""
    toolsUsed: list[str] = Field(default_factory=list)
