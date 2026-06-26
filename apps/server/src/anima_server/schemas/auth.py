"""Backward-compatible auth schema module."""

from __future__ import annotations

from anima_server.contracts.auth import (
    ChangePasswordRequest,
    ChangePasswordResponse,
    CreateAIChatMessage,
    CreateAIChatRequest,
    CreateAIChatResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RecoverRequest,
    RecoverResponse,
    RegisterRequest,
    RegisterResponse,
    UserResponse,
)

__all__ = [
    "ChangePasswordRequest",
    "ChangePasswordResponse",
    "CreateAIChatMessage",
    "CreateAIChatRequest",
    "CreateAIChatResponse",
    "LoginRequest",
    "LoginResponse",
    "LogoutResponse",
    "RecoverRequest",
    "RecoverResponse",
    "RegisterRequest",
    "RegisterResponse",
    "UserResponse",
]
