from __future__ import annotations

from logging.config import fileConfig

# Import models so that RuntimeBase.metadata is fully populated.
import anima_server.models.runtime
import anima_server.models.runtime_embedding  # noqa: F401
from alembic import context
from anima_server.db.runtime_base import RuntimeBase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = RuntimeBase.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection", None)

    if connectable is not None:
        # Programmatic usage: connection passed from ensure_runtime_tables
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        # CLI usage: alembic -c alembic_runtime.ini upgrade head
        from sqlalchemy import create_engine

        url = config.get_main_option("sqlalchemy.url")
        engine = create_engine(url)

        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                compare_server_default=True,
            )
            with context.begin_transaction():
                context.run_migrations()

        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
