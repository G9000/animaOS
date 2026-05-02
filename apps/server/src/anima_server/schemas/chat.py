from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    userId: int = Field(ge=0)
    threadId: int | None = Field(default=None, ge=1)
    stream: bool = False
    source: str | None = None
    threadId: int | None = None


class RetrievalCitation(BaseModel):
    index: int
    memoryItemId: int
    uri: str
    score: float | None = None
    category: str | None = None


class RetrievalContextFragment(BaseModel):
    rank: int
    memoryItemId: int
    uri: str
    text: str
    score: float | None = None
    category: str | None = None


class RetrievalStats(BaseModel):
    retrievalMs: float | None = None
    totalConsidered: int = 0
    returned: int = 0
    cutoffIndex: int = 0
    cutoffScore: float | None = None
    topScore: float | None = None
    cutoffRatio: float | None = None
    triggeredBy: str = ""


class RetrievalTrace(BaseModel):
    retriever: str
    citations: list[RetrievalCitation] = Field(default_factory=list)
    contextFragments: list[RetrievalContextFragment] = Field(default_factory=list)
    stats: RetrievalStats | None = None


class TokenUsage(BaseModel):
    promptTokens: int | None = None
    completionTokens: int | None = None
    totalTokens: int | None = None
    reasoningTokens: int | None = None
    cachedInputTokens: int | None = None


class ChatResponse(BaseModel):
    response: str
    model: str
    provider: str
    toolsUsed: list[str] = Field(default_factory=list)
    retrieval: RetrievalTrace | None = None
    usage: TokenUsage | None = None


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
    retrieval: RetrievalTrace | None = None


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
    retrieval: RetrievalTrace | None = None
    usage: TokenUsage | None = None
