from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)
    name: str = Field(min_length=1)
    personaTemplate: Literal["default", "alice"] = "default"
    agentName: str = Field(default="Anima", min_length=1, max_length=50)
    userDirective: str = Field(default="")


class CreateAIChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class CreateAIChatRequest(BaseModel):
    messages: list[CreateAIChatMessage]
    ownerName: str = Field(min_length=1)


class CreateAIChatResponse(BaseModel):
    message: str
    done: bool = False
    soulData: dict[str, str] | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    oldPassword: str = Field(min_length=1)
    newPassword: str = Field(min_length=6)


class UserResponse(BaseModel):
    id: int
    username: str
    name: str
    gender: str | None = None
    age: int | None = None
    birthday: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class RegisterResponse(UserResponse):
    unlockToken: str


class LoginResponse(UserResponse):
    unlockToken: str
    message: str


class LogoutResponse(BaseModel):
    success: bool


class ChangePasswordResponse(BaseModel):
    success: bool
    unlockToken: str
