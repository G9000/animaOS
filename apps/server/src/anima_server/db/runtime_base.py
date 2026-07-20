from sqlalchemy import BigInteger, MetaData
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase


@compiles(BigInteger, "sqlite")
def _compile_biginteger_sqlite(type_: BigInteger, compiler: object, **kw: object) -> str:
    """Emit ``INTEGER`` for ``BigInteger`` on SQLite.

    Runtime models use ``BigInteger`` primary keys (sized for the default
    PostgreSQL runtime backend). SQLite only treats a column declared exactly
    ``INTEGER`` as an alias for the auto-incrementing rowid — a ``BIGINT``
    primary key does NOT autoincrement, so inserting a row without an explicit
    id raises ``NOT NULL constraint failed: <table>.id``. When the runtime
    store is SQLite (a supported backend — see ``RuntimeDatabaseEngine.SQLITE``)
    every runtime table would otherwise fail its first insert. Emitting plain
    ``INTEGER`` restores autoincrement; SQLite's ``INTEGER`` is a full 64-bit
    value, so there is no range loss. Registered here on ``RuntimeBase`` so it
    is active in production (the test suite has an identical hook in conftest).
    """
    return "INTEGER"


RUNTIME_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class RuntimeBase(DeclarativeBase):
    """Base class for Runtime (PostgreSQL) models."""

    metadata = MetaData(naming_convention=RUNTIME_NAMING_CONVENTION)
