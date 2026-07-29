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
from anima_server.services.corefs import logical
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    ReadinessState,
)
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
    corefs_session: object
    keys: object
    runtime_index: CoreFSProgressiveIndex | None = None


@dataclass(frozen=True, slots=True)
class CoreFsSearchRuntimeState:
    state: logical.CoreFsSearchState
    index_generation: int | None = None


def _resolve_request_context(session: UnlockSession) -> CoreFsRequestContext:
    if session.corefs_keys is None or session.corefs_session is None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "code": "corefs_key_material_unavailable",
                "message": "CoreFS key material is unavailable. Please sign in again.",
            },
        )
    return CoreFsRequestContext(
        corefs_session=session.corefs_session,
        keys=session.corefs_keys,
        runtime_index=getattr(session, "runtime_index", None),
    )


def _principal_from_authenticated_broker(
    request: Request,
    session: UnlockSession,
) -> CoreFsPrincipal | None:
    principal = getattr(request.state, "corefs_principal", None)
    if principal is None:
        return None
    if not isinstance(principal, CoreFsPrincipal):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "corefs_invalid_broker_principal"},
        )
    if principal.user_id != session.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "corefs_broker_principal_user_mismatch"},
        )
    if principal.kind not in {"user", "anima", "client"}:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "corefs_invalid_broker_principal"},
        )
    if principal.kind == "client" and (not principal.id or not principal.install_digest):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "corefs_invalid_broker_principal"},
        )
    return principal


def _resolve_principal(request: Request, session: UnlockSession) -> CoreFsPrincipal:
    caller_identity_headers = (
        "x-anima-corefs-principal",
        "x-anima-corefs-client-id",
        "x-anima-corefs-install-digest",
    )
    if any(request.headers.get(header) is not None for header in caller_identity_headers):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "corefs_caller_identity_forbidden",
                "message": "CoreFS principals are derived from authenticated server state.",
            },
        )

    broker_principal = _principal_from_authenticated_broker(request, session)
    if broker_principal is not None:
        return broker_principal
    return CoreFsPrincipal(kind="user", id=str(session.user_id), user_id=session.user_id)


def _resolve_search_runtime_state(
    *,
    context: CoreFsRequestContext,
    selected: logical.CoreFsValidationSnapshot,
) -> CoreFsSearchRuntimeState:
    index = context.runtime_index
    if index is None:
        return CoreFsSearchRuntimeState(state="missing")
    snapshot = index.snapshot()
    if snapshot.state is ReadinessState.LOCKED:
        return CoreFsSearchRuntimeState(state="missing")
    if snapshot.catalog_generation is None:
        return CoreFsSearchRuntimeState(state="building")
    if snapshot.catalog_generation != selected.generation:
        return CoreFsSearchRuntimeState(
            state="building",
            index_generation=snapshot.catalog_generation,
        )
    if snapshot.state is ReadinessState.CATALOG_READY_DEGRADED or any(
        family.degraded for family in snapshot.families.values()
    ):
        state: logical.CoreFsSearchState = "degraded"
    elif snapshot.state is ReadinessState.READY:
        state = "ready"
    else:
        state = "building"
    return CoreFsSearchRuntimeState(
        state=state,
        index_generation=snapshot.catalog_generation,
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


def _validate_cursor_generation(
    payload: CoreFsOperationRequest,
    selected: logical.CoreFsValidationSnapshot,
) -> None:
    if payload.cursorGeneration is None:
        return
    if payload.cursorGeneration != selected.generation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "corefs_cursor_generation_mismatch",
                "cursorGeneration": payload.cursorGeneration,
                "selectedGeneration": selected.generation,
            },
        )


def _logical_http_exception(exc: ValueError) -> HTTPException | None:
    message = str(exc)
    mappings = (
        (
            "CoreFS validation snapshot is missing",
            status.HTTP_409_CONFLICT,
            "corefs_validation_snapshot_missing",
        ),
        (
            "CoreFS validation snapshot no longer matches selected generation/catalog hash",
            status.HTTP_409_CONFLICT,
            "corefs_validation_snapshot_stale",
        ),
        ("logical path was not found:", status.HTTP_404_NOT_FOUND, "corefs_path_not_found"),
        ("logical path is not a file:", status.HTTP_409_CONFLICT, "corefs_not_file"),
        ("logical path is not a directory:", status.HTTP_409_CONFLICT, "corefs_not_directory"),
        ("cursor generation ", status.HTTP_409_CONFLICT, "corefs_cursor_generation_mismatch"),
        ("invalid operation limit:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid path ", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("path is for ", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid glob pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid grep pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid grep_limit pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid literal pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid regex pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("cannot read ", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
    )
    for prefix, status_code, code in mappings:
        if message.startswith(prefix):
            return HTTPException(
                status_code=status_code,
                detail={"code": code, "message": message},
            )
    if (
        " response item requires " in message
        or (message.startswith("requested ") and " response bytes exceeds maximum " in message)
    ):
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "corefs_response_too_large", "message": message},
        )
    if message.endswith("pagination cannot produce an advancing continuation cursor"):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "corefs_cursor_cannot_advance", "message": message},
        )
    return None


def _dispatch_read(
    payload: CoreFsOperationRequest,
    *,
    context: CoreFsRequestContext,
    selected: logical.CoreFsValidationSnapshot,
) -> dict[str, Any] | None:
    _validate_cursor_generation(payload, selected)
    common = {
        "corefs_session": context.corefs_session,
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
        runtime_state = _resolve_search_runtime_state(context=context, selected=selected)
        return _decode_logical_response(
            logical.search_readiness_v1(
                **common,
                state=runtime_state.state,
                index_generation=runtime_state.index_generation,
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
def run_corefs_operation(
    payload: CoreFsOperationRequest,
    request: Request,
) -> CoreFsOperationResponse:
    session = require_unlocked_session(request)
    principal = _resolve_principal(request, session)

    is_write_operation = payload.operation in _WRITE_OPERATIONS
    if not is_write_operation and payload.operation not in _READ_OPERATIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "corefs_unknown_operation"},
        )

    if principal.kind == "client":
        _client_grant_required(principal)

    context = _resolve_request_context(session)
    if is_write_operation:
        return CoreFsOperationResponse(
            principal=principal.to_response(),
            operation=payload.operation,
            selected=None,
            result=logical.frozen_mutation_result(payload.operation),
        )

    try:
        selected = logical.select_validation_snapshot(
            corefs_session=context.corefs_session,
            keys=context.keys,
        )
        result = _dispatch_read(payload, context=context, selected=selected)
    except ValueError as exc:
        http_error = _logical_http_exception(exc)
        if http_error is None:
            raise
        raise http_error from exc
    return CoreFsOperationResponse(
        principal=principal.to_response(),
        operation=payload.operation,
        selected=_selected_response(selected),
        result=result,
    )
