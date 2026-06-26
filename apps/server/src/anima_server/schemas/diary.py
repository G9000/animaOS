from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class DiaryEntryCreateRequest(BaseModel):
    userId: int = Field(ge=0)
    entryDate: str = Field(min_length=10, max_length=10)
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=50_000)
    mood: str | None = Field(default=None, max_length=80)

    @field_validator("entryDate")
    @classmethod
    def _validate_entry_date(cls, value: str) -> str:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError("entryDate must use YYYY-MM-DD format")
        year, month, day = value.split("-")
        if not (year.isdigit() and month.isdigit() and day.isdigit()):
            raise ValueError("entryDate must use YYYY-MM-DD format")
        return value

    @field_validator("title", "body", "mood")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped


class DiaryAttachmentResponse(BaseModel):
    id: int
    entryId: int
    kind: str
    mimeType: str
    filename: str | None = None
    caption: str | None = None
    sizeBytes: int
    sha256: str
    createdAt: datetime | None = None
    url: str


class DiaryEntryResponse(BaseModel):
    id: int
    userId: int
    entryDate: str
    title: str | None = None
    body: str
    mood: str | None = None
    source: str
    attachments: list[DiaryAttachmentResponse] = Field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
