from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from anima_server.api.deps.unlock import require_unlocked_session
from anima_server.api.deps.db_mode import require_sqlite_mode
from anima_server.db import get_db

router = APIRouter(prefix="/api/db", tags=["db"])

MAX_ROWS = 500

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _validate_table(insp: Any, table_name: str) -> None:
    """Raise 404 when *table_name* does not exist."""
    if table_name not in insp.get_table_names():
        raise HTTPException(
            status_code=404, detail=f"Table '{table_name}' not found"
        )


def _pk_columns(insp: Any, table_name: str) -> list[str]:
    pk = insp.get_pk_constraint(table_name)
    return list(pk.get("constrained_columns") or [])


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


@router.get("/tables/{table_name}")
def get_table_rows(
    table_name: str,
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
    limit: int = Query(default=100, ge=1, le=MAX_ROWS),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    require_unlocked_session(request)
    insp = inspect(db.get_bind())
    _validate_table(insp, table_name)

    columns = [col["name"] for col in insp.get_columns(table_name)]
    pk_cols = _pk_columns(insp, table_name)
    total = db.execute(
        text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar() or 0
    rows_raw = db.execute(
        text(f'SELECT * FROM "{table_name}" LIMIT :lim OFFSET :off'),
        {"lim": limit, "off": offset},
    ).fetchall()
    rows = [dict(zip(columns, row)) for row in rows_raw]
    return {
        "table": table_name,
        "columns": columns,
        "primaryKeys": pk_cols,
        "rows": rows,
        "total": total,
    }


@router.post("/query")
def run_query(
    request: Request,
    body: dict[str, str],
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    require_unlocked_session(request)
    sql = (body.get("sql") or "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Empty query")

    # Only allow SELECT statements for safety
    first_word = sql.split()[0].upper() if sql.split() else ""
    if first_word not in ("SELECT", "PRAGMA", "EXPLAIN"):
        raise HTTPException(
            status_code=400, detail="Only SELECT, PRAGMA, and EXPLAIN queries are allowed")

    result = db.execute(text(sql))
    columns = list(result.keys()) if result.returns_rows else []
    rows = [dict(zip(columns, row))
            for row in result.fetchall()] if result.returns_rows else []
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
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    require_unlocked_session(request)
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
    request: Request,
    _mode: None = Depends(require_sqlite_mode),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    require_unlocked_session(request)
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
