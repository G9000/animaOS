from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PresenceConfigResponse(BaseModel):
    userId: int
    enabled: bool
    mainChatEnabled: bool
    homeGreetingContextEnabled: bool
    taskNudgesEnabled: bool
    memoryNudgesEnabled: bool
    checkInNudgesEnabled: bool
    customInstruction: str | None = None


class PresenceConfigUpdateRequest(BaseModel):
    enabled: bool | None = None
    mainChatEnabled: bool | None = None
    homeGreetingContextEnabled: bool | None = None
    taskNudgesEnabled: bool | None = None
    memoryNudgesEnabled: bool | None = None
    checkInNudgesEnabled: bool | None = None
    customInstruction: str | None = Field(default=None, max_length=500)

    @field_validator("customInstruction")
    @classmethod
    def normalize_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None
