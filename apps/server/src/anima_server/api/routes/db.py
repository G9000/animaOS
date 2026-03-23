from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from anima_server.api.deps.db_mode import require_sqlite_mode
from anima_server.api.deps.unlock import read_unlock_token, require_unlocked_session
from anima_server.db import get_db
from anima_server.models import User
from anima_server.services.auth import verify_password
from anima_server.services.crypto import (
    ENCRYPTED_TEXT_PREFIX,
    ENCRYPTED_TEXT_PREFIX_AAD,
    decrypt_text_with_dek,
)
from anima_server.services.data_crypto import resolve_domain
from anima_server.services.sessions import UnlockSession, unlock_session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/db", tags=["db"])

MAX_ROWS = 500
DB_VIEWER_REAUTH_WINDOW_SECONDS = 300
PROTECTED_TABLES = {"users", "user_keys", "alembic_version"}

# Map (actual_table, actual_column) → (aad_table, aad_field) for columns
# where encryption used different table/field names than the real DB schema.
_AAD_OVERRIDES: dict[tuple[str, str], tuple[str, str]] = {
    ("memory_claims", "value_text"): ("memory_items", "content"),
}

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _validate_table(insp: Any, table_name: str) -> None:
    """Raise 404 when *table_name* does not exist."""
    if table_name not in insp.get_table_names():
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")


def _pk_columns(insp: Any, table_name: str) -> list[str]:
    pk = insp.get_pk_constraint(table_name)
    return list(pk.get("constrained_columns") or [])


def _is_encrypted(value: object) -> bool:
    """Return True if *value* looks like a field-level encrypted string."""
    if not isinstance(value, str):
        return False
    return value.startswith(f"{ENCRYPTED_TEXT_PREFIX}:") or value.startswith(
        f"{ENCRYPTED_TEXT_PREFIX_AAD}:"
    )


_FROM_RE = re.compile(
    r'\bFROM\s+"?(\w+)"?',
    re.IGNORECASE,
)
_BLOCKED_QUERY_KEYWORDS_RE = re.compile(
    r"\b(?:ATTACH|DETACH|load_extension)\b",
    re.IGNORECASE,
)
_FALLBACK_UNSAFE_QUERY_RE = re.compile(
    r"\b(?:ALTER|ANALYZE|CREATE|DELETE|DROP|INSERT|REINDEX|REPLACE|SAVEPOINT|UPDATE|VACUUM)\b",
    re.IGNORECASE,
)
_PROTECTED_TABLE_QUERY_RE = re.compile(
    r'(?<!\w)(?:"(?:alembic_version|user_keys|users)"|(?:alembic_version|user_keys|users))(?!\w)',
    re.IGNORECASE,
)
_SQLITE_DENY_ACTIONS = {
    getattr(sqlite3, name)
    for name in (
        "SQLITE_ALTER_TABLE",
        "SQLITE_ANALYZE",
        "SQLITE_ATTACH",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DELETE",
        "SQLITE_DETACH",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_INSERT",
        "SQLITE_PRAGMA",
        "SQLITE_REINDEX",
        "SQLITE_TRANSACTION",
        "SQLITE_UPDATE",
    )
    if hasattr(sqlite3, name)
}


def _extract_table_name(sql: str) -> str | None:
    """Best-effort extract a single table name from a SQL query."""
    m = _FROM_RE.search(sql)
    return m.group(1) if m else None


def _ensure_table_not_protected(table_name: str) -> None:
    if table_name in PROTECTED_TABLES:
        raise HTTPException(
            status_code=403,
            detail=f"Table '{table_name}' is protected",
        )


def _validate_query_sql(sql: str) -> None:
    if ";" in sql.rstrip():
        raise HTTPException(status_code=400, detail="Semicolons are not allowed")
    if _BLOCKED_QUERY_KEYWORDS_RE.search(sql):
        raise HTTPException(status_code=400, detail="Query contains blocked keywords")
    if _PROTECTED_TABLE_QUERY_RE.search(sql):
        raise HTTPException(status_code=403, detail="Query references a protected table")


def _query_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    _db_name: str | None,
    _trigger_name: str | None,
) -> int:
    if action == getattr(sqlite3, "SQLITE_FUNCTION", -1):
        function_name = (arg2 or arg1 or "").lower()
        if function_name == "load_extension":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == getattr(sqlite3, "SQLITE_READ", -1) and (arg1 or "") in PROTECTED_TABLES:
        return sqlite3.SQLITE_DENY
    if action in _SQLITE_DENY_ACTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _execute_read_only_query(db: Session, sql: str):
    connection = db.connection().connection
    dbapi_connection = getattr(connection, "driver_connection", None)
    if dbapi_connection is None:
        dbapi_connection = getattr(connection, "dbapi_connection", connection)

    set_authorizer = getattr(dbapi_connection, "set_authorizer", None)
    if set_authorizer is None:
        if _FALLBACK_UNSAFE_QUERY_RE.search(sql):
            raise HTTPException(status_code=400, detail="Query contains blocked keywords")
        return db.execute(text(sql))

    set_authorizer(_query_authorizer)
    try:
        return db.execute(text(sql))
    finally:
        set_authorizer(None)


def _try_decrypt_cell(
    val: str,
    deks: list[bytes],
    aad: bytes | None,
) -> str:
    """Try to decrypt a single cell with multiple DEKs and AAD variants."""
    import logging

    logger = logging.getLogger(__name__)

    # Try each DEK with AAD first, then without
    for dek in deks:
        if aad is not None:
            try:
                return decrypt_text_with_dek(val, dek, aad=aad)
            except Exception:
                pass
        # Fallback: try without AAD (for legacy enc1 data)
        try:
            return decrypt_text_with_dek(val, dek)
        except Exception:
            pass

    # Last resort: if we have AAD, try common variations
    if aad is not None:
        # Try with different AAD formats (legacy compatibility)
        aad_str = aad.decode("utf-8")
        parts = aad_str.split(":")
        if len(parts) == 3:
            table, _user_id, field = parts
            # Try without user_id in AAD
            alt_aads = [
                f"{table}::{field}".encode(),
                f"{table}:{field}".encode(),
                field.encode("utf-8"),
            ]
            for alt_aad in alt_aads:
                for dek in deks:
                    try:
                        result = decrypt_text_with_dek(val, dek, aad=alt_aad)
                        logger.info(f"Decrypted with alternative AAD: {alt_aad}")
                        return result
                    except Exception:
                        pass

    logger.debug(f"Failed to decrypt value with {len(deks)} DEKs")
    return val


def _decrypt_rows(
    rows: list[dict[str, Any]],
    session: UnlockSession,
    table_name: str,
) -> list[dict[str, Any]]:
    """Best-effort decrypt every encrypted cell in *rows*."""
    import logging

    logger = logging.getLogger(__name__)

    if not session.deks:
        logger.warning(f"No DEKs in session for user {session.user_id}")
        return rows

    user_id = session.user_id
    # Preferred DEK first, then all others as fallback
    domain = resolve_domain(table_name)
    preferred = session.deks.get(domain)
    all_deks: list[bytes] = []
    if preferred is not None:
        all_deks.append(preferred)
    for d, dek in session.deks.items():
        if d != domain:
            all_deks.append(dek)

    logger.info(
        f"Decrypting {len(rows)} rows from {table_name} with domain={domain}, deks={[d for d in session.deks]}"
    )

    if not all_deks:
        return rows

    decrypted: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for col, val in row.items():
            if _is_encrypted(val):
                aad_table, aad_field = _AAD_OVERRIDES.get(
                    (table_name, col),
                    (table_name, col),
                )
                aad = f"{aad_table}:{user_id}:{aad_field}".encode()
                new_row[col] = _try_decrypt_cell(val, all_deks, aad)
            else:
                new_row[col] = val
        decrypted.append(new_row)
    return decrypted


def _decrypt_rows_multi_domain(
    rows: list[dict[str, Any]],
    session: UnlockSession,
    table_name: str | None = None,
) -> list[dict[str, Any]]:
    """Try every available domain DEK until one succeeds per cell."""
    if not session.deks:
        return rows

    user_id = session.user_id
    all_deks = list(session.deks.values())

    decrypted: list[dict[str, Any]] = []
    for row in rows:
        new_row: dict[str, Any] = {}
        for col, val in row.items():
            if _is_encrypted(val):
                if table_name:
                    aad_table, aad_field = _AAD_OVERRIDES.get(
                        (table_name, col),
                        (table_name, col),
                    )
                    aad = f"{aad_table}:{user_id}:{aad_field}".encode()
                else:
                    aad = None
                new_row[col] = _try_decrypt_cell(val, all_deks, aad)
            else:
                new_row[col] = val
        decrypted.append(new_row)
    return decrypted


def require_db_viewer_auth(request: Request) -> UnlockSession:
    session = require_unlocked_session(request)
    token = read_unlock_token(request)
    verified_at = unlock_session_store.get_db_viewer_verified_at(token)
    if verified_at is None:
        raise HTTPException(
            status_code=403,
            detail="Password verification required for DB viewer access.",
        )
    if time.time() - verified_at > DB_VIEWER_REAUTH_WINDOW_SECONDS:
        unlock_session_store.set_db_viewer_verified_at(token, None)
        raise HTTPException(
            status_code=403,
            detail="Password verification expired for DB viewer access.",
        )
    return session


# --------------------------------------------------------------------------- #
# Read endpoints
# --------------------------------------------------------------------------- #


@router.get("/tables")
def list_tables(
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    require_unlocked_session(request)
    insp = inspect(db.get_bind())
    tables = []
    for name in sorted(insp.get_table_names()):
        row_count = db.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
        tables.append({"name": name, "rowCount": row_count or 0})
    return tables


class VerifyPasswordRequest(BaseModel):
    password: str


@router.post("/verify-password")
def verify_db_password(
    body: VerifyPasswordRequest,
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Re-verify the user's password before exposing decrypted DB content."""
    session = require_unlocked_session(request)
    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    result = verify_password(body.password, user.password_hash)
    if not result.valid:
        raise HTTPException(status_code=401, detail="Invalid password")
    unlock_session_store.set_db_viewer_verified_at(read_unlock_token(request), time.time())
    return {"verified": True}


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    default: str | None
    primaryKey: bool


@router.get("/tables/{table_name}")
def get_table_rows(
    table_name: str,
    session: UnlockSession = Depends(require_db_viewer_auth),
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=MAX_ROWS),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    insp = inspect(db.get_bind())
    _validate_table(insp, table_name)

    columns = [col["name"] for col in insp.get_columns(table_name)]
    pk_cols = _pk_columns(insp, table_name)
    total = db.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0
    rows_raw = db.execute(
        text(f'SELECT * FROM "{table_name}" LIMIT :lim OFFSET :off'),
        {"lim": limit, "off": offset},
    ).fetchall()
    rows = [dict(zip(columns, row, strict=False)) for row in rows_raw]
    rows = _decrypt_rows(rows, session, table_name)
    return {
        "table": table_name,
        "columns": columns,
        "primaryKeys": pk_cols,
        "rows": rows,
        "total": total,
    }


@router.get("/tables/{table_name}/schema")
def get_table_schema(
    table_name: str,
    _session: UnlockSession = Depends(require_db_viewer_auth),
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Get detailed schema information for a table."""
    insp = inspect(db.get_bind())
    _validate_table(insp, table_name)

    # Get columns with detailed info
    columns_raw = insp.get_columns(table_name)
    pk_cols = set(_pk_columns(insp, table_name))

    columns: list[dict[str, object]] = []
    for col in columns_raw:
        columns.append(
            {
                "name": col["name"],
                "type": str(col["type"]),
                "nullable": col.get("nullable", True),
                "default": col.get("default"),
                "primaryKey": col["name"] in pk_cols,
            }
        )

    # Get indexes
    indexes_raw = insp.get_indexes(table_name)
    indexes = [idx["name"] for idx in indexes_raw if idx.get("name")]

    return {
        "columns": columns,
        "indexes": indexes,
    }


@router.post("/query")
def run_query(
    body: dict[str, str],
    session: UnlockSession = Depends(require_db_viewer_auth),
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    sql = (body.get("sql") or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Empty query")

    # Only allow SELECT statements for safety
    first_word = sql.split()[0].upper() if sql.split() else ""
    if first_word not in ("SELECT", "PRAGMA", "EXPLAIN"):
        raise HTTPException(
            status_code=400, detail="Only SELECT, PRAGMA, and EXPLAIN queries are allowed"
        )

    _validate_query_sql(sql)
    result = _execute_read_only_query(db, sql)
    columns = list(result.keys()) if result.returns_rows else []
    rows = (
        [dict(zip(columns, row, strict=False)) for row in result.fetchall()]
        if result.returns_rows
        else []
    )

    # Best-effort extract table name from simple "SELECT ... FROM table" queries
    # so we can reconstruct AAD for decryption.
    table_name = _extract_table_name(sql)
    rows = _decrypt_rows_multi_domain(rows, session, table_name=table_name)
    return {"columns": columns, "rows": rows, "rowCount": len(rows)}


# --------------------------------------------------------------------------- #
# Mutation endpoints (edit / delete)
# --------------------------------------------------------------------------- #


class RowConditions(BaseModel):
    conditions: dict[str, Any]


class RowUpdate(BaseModel):
    conditions: dict[str, Any]
    updates: dict[str, Any]


@router.delete("/tables/{table_name}/rows")
def delete_row(
    table_name: str,
    body: RowConditions,
    _session: UnlockSession = Depends(require_db_viewer_auth),
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ensure_table_not_protected(table_name)
    insp = inspect(db.get_bind())
    _validate_table(insp, table_name)

    if not body.conditions:
        raise HTTPException(status_code=400, detail="Conditions required")

    where_parts: list[str] = []
    params: dict[str, Any] = {}
    for i, (col, val) in enumerate(body.conditions.items()):
        placeholder = f"w{i}"
        if val is None:
            where_parts.append(f'"{col}" IS NULL')
        else:
            where_parts.append(f'"{col}" = :{placeholder}')
            params[placeholder] = val

    where_clause = " AND ".join(where_parts)
    sql = f'DELETE FROM "{table_name}" WHERE {where_clause}'
    result = db.execute(text(sql), params)
    db.commit()
    return {"deleted": result.rowcount}


@router.put("/tables/{table_name}/rows")
def update_row(
    table_name: str,
    body: RowUpdate,
    _session: UnlockSession = Depends(require_db_viewer_auth),
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _ensure_table_not_protected(table_name)
    insp = inspect(db.get_bind())
    _validate_table(insp, table_name)

    if not body.conditions:
        raise HTTPException(status_code=400, detail="Conditions required")
    if not body.updates:
        raise HTTPException(status_code=400, detail="Updates required")

    set_parts: list[str] = []
    params: dict[str, Any] = {}
    for i, (col, val) in enumerate(body.updates.items()):
        placeholder = f"s{i}"
        set_parts.append(f'"{col}" = :{placeholder}')
        params[placeholder] = val

    where_parts: list[str] = []
    for i, (col, val) in enumerate(body.conditions.items()):
        placeholder = f"w{i}"
        if val is None:
            where_parts.append(f'"{col}" IS NULL')
        else:
            where_parts.append(f'"{col}" = :{placeholder}')
            params[placeholder] = val

    set_clause = ", ".join(set_parts)
    where_clause = " AND ".join(where_parts)
    sql = f'UPDATE "{table_name}" SET {set_clause} WHERE {where_clause}'
    result = db.execute(text(sql), params)
    db.commit()
    return {"updated": result.rowcount}
