from __future__ import annotations

import base64
import binascii
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request, status

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.schemas.corefs import (
    CoreFsOperationRequest,
    CoreFsOperationResponse,
    CoreFsPrincipalResponse,
    CoreFsSelectedSnapshotResponse,
)
from anima_server.services.corefs import logical
from anima_server.services.corefs.client_access import (
    ClientAccessError,
    ClientCapabilityIdentity,
    ClientScope,
    CoreFsFolderGrantTarget,
    authorize_client_path,
    client_capability_broker,
    list_corefs_grant_folders,
)
from anima_server.services.corefs.indexer import (
    CoreFSProgressiveIndex,
    CoreFSRuntimeLocked,
    IndexCapability,
    ReadinessState,
)
from anima_server.services.corefs.migration import (
    embed_configured_query,
    initialize_catalog_if_idle,
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
    "search",
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
_MAX_MUTATION_BODY_BYTES = 16 * 1024 * 1024

# This flips only after every PCF-008 content-family adapter and the funded
# signed-package gate are evidenced. Keeping the dispatch path testable while
# this is false prevents a local build from consuming the irreversible marker.
CORE_FS_PUBLIC_MUTATION_ADAPTERS_READY = False


@dataclass(frozen=True, slots=True)
class CoreFsPrincipal:
    kind: str
    id: str
    user_id: int
    install_digest: str | None = None
    installation_id: str | None = None
    package_id: str | None = None

    def to_response(self) -> CoreFsPrincipalResponse:
        return CoreFsPrincipalResponse(
            kind=self.kind,  # type: ignore[arg-type]
            id=self.id,
            userId=self.user_id,
            installDigest=self.install_digest,
            installationId=self.installation_id,
            packageId=self.package_id,
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
    capability = request.headers.get("x-anima-corefs-client-capability")
    if principal is not None and capability is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "corefs_conflicting_broker_principals"},
        )
    if capability is not None:
        try:
            identity = client_capability_broker.consume(
                token=capability,
                user_id=session.user_id,
                session=session,
            )
        except ClientAccessError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "corefs_client_capability_invalid", "message": str(exc)},
            ) from exc
        return CoreFsPrincipal(
            kind="client",
            id=identity.client_id,
            user_id=identity.user_id,
            install_digest=identity.install_digest,
            installation_id=identity.installation_id,
            package_id=identity.package_id,
        )
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
    if snapshot.catalog_generation is None:
        initialize_catalog_if_idle(index, selected.generation)
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


def _client_grant_required(principal: CoreFsPrincipal) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "corefs_client_grant_required",
            "principal": principal.to_response().model_dump(exclude_none=True),
        },
    )


def _client_logical_path(payload: CoreFsOperationRequest) -> str | None:
    if payload.operation in {"apply_patch", "move", "trash", "restore"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "corefs_client_multi_target_mutation_unavailable",
                "message": "Client structural mutations require atomic authorization of every target.",
            },
        )
    if payload.operation in {"walk", "glob", "grep"}:
        return _require_path(payload, field="root")
    if payload.operation == "search_readiness":
        return None
    if payload.operation == "search":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "corefs_client_search_not_folder_scoped",
                "message": "Client search is unavailable until results can be filtered by grant.",
            },
        )
    return _require_path(payload)


def _client_required_scope(operation: str) -> ClientScope:
    if operation in {"move", "trash", "restore"}:
        return "manage"
    if operation in _WRITE_OPERATIONS:
        return "write"
    return "read"


def _client_identity(principal: CoreFsPrincipal) -> ClientCapabilityIdentity:
    if (
        principal.installation_id is None
        or principal.install_digest is None
        or principal.package_id is None
    ):
        _client_grant_required(principal)
    return ClientCapabilityIdentity(
        installation_id=principal.installation_id,
        client_id=principal.id,
        package_id=principal.package_id,
        install_digest=principal.install_digest,
        user_id=principal.user_id,
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
    mutation_mappings = {
        "corefs_mutation_invalid_path": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "corefs_mutation_invalid_content": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "corefs_mutation_invalid_patch": status.HTTP_422_UNPROCESSABLE_CONTENT,
        "corefs_mutation_size_limit": status.HTTP_413_CONTENT_TOO_LARGE,
        "corefs_mutation_not_found": status.HTTP_404_NOT_FOUND,
        "corefs_mutation_wrong_entry_kind": status.HTTP_409_CONFLICT,
        "corefs_mutation_collision": status.HTTP_409_CONFLICT,
        "corefs_mutation_revision_conflict": status.HTTP_409_CONFLICT,
        "corefs_mutation_role_collision": status.HTTP_409_CONFLICT,
        "corefs_mutation_invalid_lifecycle": status.HTTP_409_CONFLICT,
        "corefs_mutation_source_descendant": status.HTTP_409_CONFLICT,
        "corefs_mutation_missing_expected_revision": status.HTTP_409_CONFLICT,
        "corefs_mutation_optimistic_conflict": status.HTTP_409_CONFLICT,
        "corefs_mutation_policy_denied": status.HTTP_403_FORBIDDEN,
        "corefs_mutation_policy_boundary_mismatch": status.HTTP_403_FORBIDDEN,
        "corefs_mutation_reserved_role_requires_user": status.HTTP_403_FORBIDDEN,
        "corefs_mutation_prepare_failed": status.HTTP_503_SERVICE_UNAVAILABLE,
        "corefs_mutation_storage_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    }
    if message in mutation_mappings:
        return HTTPException(
            status_code=mutation_mappings[message],
            detail={"code": message},
        )
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
        (
            "invalid operation limit:",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "corefs_invalid_request",
        ),
        ("invalid path ", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("path is for ", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid glob pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("invalid grep pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        (
            "invalid grep_limit pattern:",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "corefs_invalid_request",
        ),
        (
            "invalid literal pattern:",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "corefs_invalid_request",
        ),
        ("invalid regex pattern:", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
        ("cannot read ", status.HTTP_422_UNPROCESSABLE_CONTENT, "corefs_invalid_request"),
    )
    for prefix, status_code, code in mappings:
        if message.startswith(prefix):
            return HTTPException(
                status_code=status_code,
                detail={"code": code, "message": message},
            )
    if " response item requires " in message or (
        message.startswith("requested ") and " response bytes exceeds maximum " in message
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


def _mutation_target(payload: CoreFsOperationRequest) -> dict[str, str]:
    if payload.stableId is not None:
        return {"stableId": payload.stableId}
    return {"path": _require_path(payload)}


def _mutation_trash_folder(payload: CoreFsOperationRequest) -> dict[str, str]:
    if payload.trashFolderStableId is not None:
        return {"stableId": payload.trashFolderStableId}
    if payload.trashFolderPath is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "corefs_trash_folder_required"},
        )
    return {"path": payload.trashFolderPath}


def _decode_mutation_body(payload: CoreFsOperationRequest) -> bytes:
    if payload.contentBase64 is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "corefs_mutation_body_required"},
        )
    try:
        body = base64.b64decode(payload.contentBase64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "corefs_mutation_body_invalid"},
        ) from exc
    if base64.b64encode(body).decode("ascii") != payload.contentBase64:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "corefs_mutation_body_noncanonical"},
        )
    if len(body) > _MAX_MUTATION_BODY_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={"code": "corefs_mutation_body_too_large"},
        )
    return body


def _dispatch_write(
    payload: CoreFsOperationRequest,
    *,
    context: CoreFsRequestContext,
    selected: logical.CoreFsValidationSnapshot,
    principal: CoreFsPrincipal,
) -> dict[str, object]:
    mutation: dict[str, object]
    body: bytes | None = None
    if payload.operation == "mkdir":
        mutation = {"operation": "mkdir", "path": _require_path(payload)}
        if payload.reservedRole is not None:
            if principal.kind != "user":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "corefs_reserved_role_requires_user"},
                )
            mutation["reservedRole"] = payload.reservedRole
    elif payload.operation == "create_file":
        body = _decode_mutation_body(payload)
        mutation = {
            "operation": "create_file",
            "path": _require_path(payload),
            "kind": payload.kind,
            "contentType": payload.contentType,
            "bodyEncoding": payload.bodyEncoding,
        }
    elif payload.operation == "write_file":
        body = _decode_mutation_body(payload)
        mutation = {
            "operation": "write_file",
            "target": _mutation_target(payload),
            "expectedRevision": payload.expectedRevision,
            "contentType": payload.contentType,
            "bodyEncoding": payload.bodyEncoding,
        }
    elif payload.operation == "apply_patch":
        mutation = {
            "operation": "apply_patch",
            "patch": payload.patch,
            "expectedRevisions": payload.expectedRevisions,
            "addFormats": {
                path: value.model_dump() for path, value in (payload.addFormats or {}).items()
            },
            "trashFolder": _mutation_trash_folder(payload),
        }
    elif payload.operation == "move":
        mutation = {
            "operation": "move",
            "source": _mutation_target(payload),
            "destination": payload.destination,
            "expectedRevision": payload.expectedRevision,
        }
    elif payload.operation == "trash":
        mutation = {
            "operation": "trash",
            "target": _mutation_target(payload),
            "trashFolder": _mutation_trash_folder(payload),
            "expectedRevision": payload.expectedRevision,
        }
    elif payload.operation == "restore":
        mutation = {
            "operation": "restore",
            "target": _mutation_target(payload),
            "destination": payload.destination,
            "expectedRevision": payload.expectedRevision,
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "corefs_unknown_write_operation"},
        )

    invalidate = (
        (lambda _generation, _catalog_hash: context.runtime_index.begin_catalog())
        if context.runtime_index is not None
        else None
    )
    return logical.execute_mutation_v1(
        corefs_session=context.corefs_session,
        keys=context.keys,
        selected=selected,
        principal=principal.kind,  # type: ignore[arg-type]
        mutation=mutation,
        body=body,
        invalidate=invalidate,
    )


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
    if payload.operation == "search":
        index = context.runtime_index
        if index is None or index.snapshot().state is ReadinessState.LOCKED:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"code": "corefs_search_locked"},
            )
        snapshot = index.snapshot()
        if snapshot.catalog_generation != selected.generation:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "corefs_search_generation_stale",
                    "indexGeneration": snapshot.catalog_generation,
                    "selectedGeneration": selected.generation,
                },
            )
        mode = payload.searchMode
        capability = {
            "exact": IndexCapability.EXACT_SEARCH,
            "text": IndexCapability.TEXT_SEARCH,
            "semantic": IndexCapability.SEMANTIC_SEARCH,
        }[mode]
        if capability not in snapshot.capabilities:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "corefs_search_not_ready",
                    "mode": mode,
                    "state": snapshot.state.value,
                },
            )
        query_id = index.begin_query()
        try:
            if mode == "exact":
                object_ids = index.lookup_exact(payload.query or "")[: payload.maxResults]
            elif mode == "text":
                object_ids = index.search_text(payload.query or "")[: payload.maxResults]
            else:
                vector = embed_configured_query(payload.query or "")
                object_ids = index.search_semantic(
                    vector,
                    limit=payload.maxResults,
                )
        except CoreFSRuntimeLocked as exc:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"code": "corefs_search_locked"},
            ) from exc
        except (RuntimeError, TypeError, ValueError) as exc:
            if mode != "semantic":
                raise
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "corefs_semantic_query_unavailable",
                    "message": str(exc),
                },
            ) from exc
        finally:
            with suppress(CoreFSRuntimeLocked):
                index.finish_query(query_id)
        return {
            "generation": selected.generation,
            "mode": mode,
            "objectIds": list(object_ids),
        }
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

    if principal.kind == "client" and principal.installation_id is None:
        _client_grant_required(principal)

    context = _resolve_request_context(session)
    if is_write_operation and not CORE_FS_PUBLIC_MUTATION_ADAPTERS_READY:
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
        client_authorization: (
            tuple[
                ClientCapabilityIdentity,
                tuple[CoreFsFolderGrantTarget, ...],
                str | None,
                ClientScope,
            ]
            | None
        ) = None
        if principal.kind == "client":
            identity = _client_identity(principal)
            folders = list_corefs_grant_folders(session, selected=selected)
            logical_path = _client_logical_path(payload)
            required_scope = _client_required_scope(payload.operation)
            authorize_client_path(
                identity,
                folders=folders,
                logical_path=logical_path,
                required_scope=required_scope,
            )
            client_authorization = (identity, folders, logical_path, required_scope)
        if is_write_operation:
            result = _dispatch_write(
                payload,
                context=context,
                selected=selected,
                principal=principal,
            )
        else:
            result = _dispatch_read(payload, context=context, selected=selected)
        if client_authorization is not None:
            identity, folders, logical_path, required_scope = client_authorization
            authorize_client_path(
                identity,
                folders=folders,
                logical_path=logical_path,
                required_scope=required_scope,
                record_use=True,
            )
    except ClientAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "corefs_client_grant_required", "message": str(exc)},
        ) from exc
    except logical.CoreFsMutationUnavailable as exc:
        code = str(exc)
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if code
                in {
                    "corefs_native_mutation_unavailable",
                    "corefs_native_mutation_result_invalid",
                }
                else status.HTTP_409_CONFLICT
            ),
            detail={"code": code},
        ) from exc
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
