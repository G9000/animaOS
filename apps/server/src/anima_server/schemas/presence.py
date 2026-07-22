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
    initiativeEnabled: bool = False
    quietHoursStart: int | None = None
    quietHoursEnd: int | None = None
    dreamSharing: str = "on_ask"


class PresenceConfigUpdateRequest(BaseModel):
    enabled: bool | None = None
    mainChatEnabled: bool | None = None
    homeGreetingContextEnabled: bool | None = None
    taskNudgesEnabled: bool | None = None
    memoryNudgesEnabled: bool | None = None
    checkInNudgesEnabled: bool | None = None
    customInstruction: str | None = Field(default=None, max_length=500)
    initiativeEnabled: bool | None = None
    quietHoursStart: int | None = Field(default=None, ge=0, le=23)
    quietHoursEnd: int | None = Field(default=None, ge=0, le=23)
    dreamSharing: str | None = Field(default=None, pattern="^(off|on_ask|ambient)$")

    @field_validator("customInstruction")
    @classmethod
    def normalize_instruction(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class PendingInitiativeResponse(BaseModel):
    id: int
    drive: str
    text: str
    createdAt: str
    delivered: bool
    acknowledged: bool


class PendingInitiativesResponse(BaseModel):
    userId: int
    initiatives: list[PendingInitiativeResponse]
