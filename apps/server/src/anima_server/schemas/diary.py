from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Diary bodies can embed images as base64 data URIs (Notion-style inline
# images), so the cap is sized well above plain-text needs.
DIARY_BODY_MAX_LENGTH = 20_000_000


class DiaryEntryCreateRequest(BaseModel):
    userId: int = Field(ge=0)
    entryDate: str = Field(min_length=10, max_length=10)
    title: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=DIARY_BODY_MAX_LENGTH)
    mood: str | None = Field(default=None, max_length=80)
    folderId: int | None = Field(default=None, ge=0)

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


class DiaryEntryUpdateRequest(BaseModel):
    entryDate: str | None = Field(default=None, min_length=10, max_length=10)
    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=DIARY_BODY_MAX_LENGTH)
    mood: str | None = Field(default=None, max_length=80)
    coverAttachmentId: int | None = Field(default=None, ge=0)
    folderId: int | None = Field(default=None, ge=0)
    clearTitle: bool = False
    clearMood: bool = False
    clearCover: bool = False
    clearFolder: bool = False

    @field_validator("entryDate")
    @classmethod
    def _validate_entry_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
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
    coverAttachmentId: int | None = None
    folderId: int | None = None
    attachments: list[DiaryAttachmentResponse] = Field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class DiaryFolderCreateRequest(BaseModel):
    userId: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class DiaryFolderUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped


class DiaryFolderResponse(BaseModel):
    id: int
    userId: int
    name: str
    entryCount: int = 0
    createdAt: datetime | None = None


class DiaryDraftImportRequest(BaseModel):
    userId: int = Field(ge=0)
    draftId: str = Field(min_length=1, max_length=256)
    targetEntryId: int | None = Field(default=None, ge=0)
    html: str = Field(max_length=DIARY_BODY_MAX_LENGTH)
    title: str = Field(default="", max_length=200)
    mood: str = Field(default="", max_length=80)
    entryDate: str = Field(min_length=10, max_length=10)
    updatedAt: datetime


class DiaryDraftImportResponse(BaseModel):
    stableId: str
    revision: int
    generation: int
    catalogHash: str
    verified: bool = True
    authoritative: bool = False


class DiaryCorefsPreparedResponse(BaseModel):
    generation: int
    catalogHash: str
    journalStableId: str
    notesStableId: str
    authoritative: bool = False
