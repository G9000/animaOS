from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.schemas.corefs import (
    CoreFsOperationRequest,
    CoreFsOperationResponse,
    CoreFsPrincipalResponse,
    CoreFsSelectedSnapshotResponse,
)
from anima_server.services.core import get_core_dir, get_core_id
from anima_server.services.corefs import logical
from anima_server.services.sessions import UnlockSession

router = APIRouter(prefix="/api/corefs", tags=["corefs"])

_READ_OPERATIONS = {
    "stat",
    "list",
    "walk",
    "glob",
    "grep",
    "read",
    "search_readiness",
}
_WRITE_OPERATIONS = {
    "mkdir",
    "create_file",
    "write_file",
    "apply_patch",
    "move",
    "trash",
    "restore",
}


@dataclass(frozen=True, slots=True)
class CoreFsPrincipal:
    kind: str
    id: str
    user_id: int
    install_digest: str | None = None

    def to_response(self) -> CoreFsPrincipalResponse:
        return CoreFsPrincipalResponse(
            kind=self.kind,  # type: ignore[arg-type]
            id=self.id,
            userId=self.user_id,
            installDigest=self.install_digest,
        )


@dataclass(frozen=True, slots=True)
class CoreFsRequestContext:
    core_root: str
    core_id: str
    keys: object


def _resolve_request_context(session: UnlockSession) -> CoreFsRequestContext:
    return CoreFsRequestContext(
        core_root=str(get_core_dir()),
        core_id=get_core_id(),
        keys=session.deks,
    )


def _resolve_principal(request: Request, session: UnlockSession) -> CoreFsPrincipal:
    client_id = (request.headers.get("x-anima-corefs-client-id") or "").strip()
    install_digest = (request.headers.get("x-anima-corefs-install-digest") or "").strip()
    has_client_identity = bool(client_id or install_digest)
    requested = (request.headers.get("x-anima-corefs-principal") or "").strip().lower()
    if not requested:
        requested = "client" if has_client_identity else "user"
    if requested != "client" and has_client_identity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "corefs_conflicting_principal_identity",
                "message": "Client identity headers require the client CoreFS principal.",
            },
        )
    if requested == "user":
        return CoreFsPrincipal(kind="user", id=str(session.user_id), user_id=session.user_id)
    if requested == "anima":
        return CoreFsPrincipal(kind="anima", id=f"anima:{session.user_id}", user_id=session.user_id)
    if requested == "client":
        if not client_id or not install_digest:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "corefs_client_identity_required",
                    "message": "Client CoreFS requests require client id and install digest headers.",
                },
            )
        return CoreFsPrincipal(
            kind="client",
            id=client_id,
            user_id=session.user_id,
            install_digest=install_digest,
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "corefs_unknown_principal",
            "message": "CoreFS principal must be user, anima, or client.",
        },
    )


def _require_path(payload: CoreFsOperationRequest, *, field: str = "path") -> str:
    value = getattr(payload, field)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "corefs_path_required", "field": field},
        )
    return str(value)


def _decode_logical_response(raw: bytes | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "corefs_invalid_native_response"},
        ) from exc
    if not isinstance(decoded, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "corefs_invalid_native_response"},
        )
    return decoded


def _client_grant_required(principal: CoreFsPrincipal) -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "corefs_client_grant_required",
            "principal": principal.to_response().model_dump(exclude_none=True),
        },
    )


def _selected_response(
    selected: logical.CoreFsValidationSnapshot | None,
) -> CoreFsSelectedSnapshotResponse | None:
    if selected is None:
        return None
    return CoreFsSelectedSnapshotResponse(
        generation=selected.generation,
        catalogHash=selected.catalog_hash,
    )


def _dispatch_read(
    payload: CoreFsOperationRequest,
    *,
    context: CoreFsRequestContext,
    selected: logical.CoreFsValidationSnapshot,
) -> dict[str, Any] | None:
    common = {
        "core_root": context.core_root,
        "core_id": context.core_id,
        "keys": context.keys,
        "selected": selected,
    }
    if payload.operation == "stat":
        return _decode_logical_response(logical.stat_v1(**common, path=_require_path(payload)))
    if payload.operation == "list":
        return _decode_logical_response(
            logical.list_v1(
                **common,
                path=_require_path(payload),
                cursor_after=payload.cursorAfter,
                limit=payload.limit,
                response_bytes=payload.responseBytes,
            )
        )
    if payload.operation == "walk":
        return _decode_logical_response(
            logical.walk_v1(
                **common,
                root=_require_path(payload, field="root"),
                cursor_after=payload.cursorAfter,
                page_size=payload.pageSize,
                include_directories=payload.includeDirectories,
                response_bytes=payload.responseBytes,
            )
        )
    if payload.operation == "glob":
        cursor = (
            logical.CoreFsGlobCursor(after=payload.globCursorAfter)
            if payload.globCursorAfter is not None
            else None
        )
        return _decode_logical_response(
            logical.glob_v1(
                **common,
                root=_require_path(payload, field="root"),
                pattern=payload.pattern or "",
                max_results=payload.maxResults,
                cursor=cursor,
                response_bytes=payload.responseBytes,
            )
        )
    if payload.operation == "grep":
        cursor = (
            logical.CoreFsGrepCursor(
                path=payload.grepCursorPath,
                byte_offset=payload.grepCursorByteOffset,
                walk_after=payload.grepCursorWalkAfter,
            )
            if payload.grepCursorPath is not None
            else None
        )
        return _decode_logical_response(
            logical.grep_v1(
                **common,
                root=_require_path(payload, field="root"),
                query=payload.query or "",
                regex=payload.regex,
                max_files=payload.maxFiles,
                max_matches=payload.maxMatches,
                max_line_bytes=payload.maxLineBytes,
                cursor=cursor,
                response_bytes=payload.responseBytes,
            )
        )
    if payload.operation == "read":
        return _decode_logical_response(
            logical.read_chunk_v1(
                **common,
                path=_require_path(payload),
                offset=payload.offset,
                max_bytes=payload.maxBytes,
                response_bytes=payload.responseBytes,
            )
        )
    if payload.operation == "search_readiness":
        return _decode_logical_response(
            logical.search_readiness_v1(
                **common,
                state=payload.searchState,
                index_generation=payload.indexGeneration,
            )
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"code": "corefs_unknown_read_operation"},
    )


@router.post(
    "/operation",
    response_model=CoreFsOperationResponse,
    response_model_exclude_none=True,
)
async def run_corefs_operation(
    payload: CoreFsOperationRequest,
    request: Request,
) -> CoreFsOperationResponse:
    session = require_unlocked_session(request)
    principal = _resolve_principal(request, session)

    if payload.operation in _WRITE_OPERATIONS:
        return CoreFsOperationResponse(
            principal=principal.to_response(),
            operation=payload.operation,
            selected=None,
            result=logical.frozen_mutation_result(payload.operation),
        )

    if payload.operation not in _READ_OPERATIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "corefs_unknown_operation"},
        )

    if principal.kind == "client":
        _client_grant_required(principal)

    context = _resolve_request_context(session)
    selected = logical.select_validation_snapshot(
        core_root=context.core_root,
        core_id=context.core_id,
        keys=context.keys,
    )
    result = _dispatch_read(payload, context=context, selected=selected)
    return CoreFsOperationResponse(
        principal=principal.to_response(),
        operation=payload.operation,
        selected=_selected_response(selected),
        result=result,
    )
