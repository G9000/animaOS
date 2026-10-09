"""Retire pre-release account mappings in the supported encrypted Soul.

Revision ID: 20261009_0001
Revises: 20260812_0001
"""

from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "20261009_0001"
down_revision = "20260812_0001"
branch_labels = None
depends_on = None


_DERIVED_COLUMNS = {
    "memory_items": {"embedding_json", "embedding_checksum"},
    "kg_entities": {"embedding_json", "embedding_checksum"},
    "agent_experiences": {"embedding_json"},
    "agent_skills": {"embedding_json"},
}
_SOURCE_COLUMNS = {
    "memory_episodes": {"thread_id": ("source_thread_uri", "thread", False)},
    "emotional_signals": {"thread_id": ("source_thread_uri", "thread", False)},
    "foresight_signals": {
        "source_thread_id": ("source_thread_uri", "thread", False),
        "source_message_ids_json": ("source_message_uris", "message", True),
    },
    "agent_experiences": {
        "source_thread_id": ("source_thread_uri", "thread", False),
        "source_run_id": ("source_run_uri", "run", False),
    },
    "memory_item_evidence": {
        "runtime_thread_id": ("source_thread_uri", "thread", False),
        "runtime_message_id": ("source_message_uri", "message", False),
        "runtime_message_ids_json": ("source_message_uris", "message", True),
    },
    "user_profile_field_evidence": {
        "runtime_thread_id": ("source_thread_uri", "thread", False),
        "runtime_message_id": ("source_message_uri", "message", False),
    },
}
_DERIVED_TABLES = {"memory_vectors", "experience_cluster_state"}
for _table, _fields in _SOURCE_COLUMNS.items():
    for _field, (_destination, _family, _many) in tuple(_fields.items()):
        if _family == "message":
            _fields[_field] = (_destination, "runtime-message", _many)
_SOURCE_COLUMNS["memory_episodes"]["transcript_ref"] = (
    "source_transcript_uri",
    "transcript",
    False,
)
_SOURCE_COLUMNS["memory_item_evidence"]["transcript_ref"] = (
    "source_transcript_uri",
    "transcript",
    False,
)
_APP_TABLES = {
    "agent_threads",
    "agent_runs",
    "agent_steps",
    "agent_messages",
    "diary_entries",
    "diary_folders",
    "diary_attachments",
    "tasks",
    "presence_configs",
    "telegram_links",
    "discord_links",
    "background_task_runs",
    "corefs_writing_source_state",
}


def _verify_authored_app_coverage(connection, proof):
    covered = {
        "agent_threads": set(proof.threads),
        "agent_messages": set(proof.messages),
        **proof.authored_ids,
    }
    for name, identities in covered.items():
        if name == "agent_messages":
            rows = connection.exec_driver_sql(
                "SELECT id FROM agent_messages WHERE role IN ('user', 'assistant')"
            )
        else:
            rows = connection.exec_driver_sql(f'SELECT id FROM "{name}"')
        if any(str(row.id) not in identities for row in rows):
            raise ValueError(f"Soul retirement cannot map authored provenance in {name}")


def _inventory(connection, table_names):
    inventory = {}
    for name in sorted(table_names):
        excluded = _DERIVED_COLUMNS.get(name, set()) | set(_SOURCE_COLUMNS.get(name, {}))
        excluded |= {destination for destination, _, _ in _SOURCE_COLUMNS.get(name, {}).values()}
        excluded.add("provenance_metadata")
        columns = [
            column["name"]
            for column in sa.inspect(connection).get_columns(name)
            if column["name"] not in excluded
        ]
        projection = ", ".join(f'"{column}"' for column in columns)
        rows = connection.exec_driver_sql(f'SELECT {projection} FROM "{name}"').fetchall()
        canonical = sorted(
            json.dumps(dict(zip(columns, row, strict=True)), sort_keys=True, default=str)
            for row in rows
        )
        inventory[name] = (len(rows), hashlib.sha256("\n".join(canonical).encode()).hexdigest())
    return inventory


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    table_names = set(inspector.get_table_names())
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Soul source has foreign-key violations")
    users = list(connection.exec_driver_sql("SELECT id, created_at, updated_at FROM users"))
    if len(users) > 1:
        raise ValueError("Soul retirement requires exactly one unambiguous Core owner")
    owner_id = None
    if users:
        from anima_server.services.core import get_manifest_path
        from anima_server.services.corefs.keyslots import manifest_has_versioned_key_hierarchy

        manifest = json.loads(get_manifest_path().read_text(encoding="utf-8"))
        if manifest.get("portable_core_release") != 1 or not manifest_has_versioned_key_hierarchy(
            manifest
        ):
            raise ValueError("Unsupported pre-release Soul owner format")
        if manifest.get("owner_user_id") != users[0].id:
            raise ValueError("Soul retirement owner does not match the Core manifest")
        owner_id = str(manifest["owner_id"])
        from anima_server.services.corefs.soul_provenance import current_source_proof

        proof = current_source_proof(users[0].id)
        if (
            proof is None
            or (proof.profile.owner_id if proof.profile is not None else proof.owner_id) != owner_id
        ):
            raise ValueError("Soul retirement requires authenticated Core owner/profile proof")
        from anima_server.services.corefs.soul_store import (
            SUPPORTED_SOUL_SOURCE_REVISION,
            _database_inventory,
            current_supported_recovery_copy,
        )

        receipt = current_supported_recovery_copy()
        if receipt is None:
            raise ValueError(
                "Soul retirement requires an independently verified encrypted recovery copy"
            )
        from pathlib import Path

        if (
            Path(connection.engine.url.database).resolve() != receipt.source_path
            or _database_inventory(
                connection, supported_source_revision=SUPPORTED_SOUL_SOURCE_REVISION
            )
            != receipt.inventory
        ):
            raise ValueError("Soul source changed after its verified encrypted recovery copy")
        if proof.profile is not None:
            _verify_authored_app_coverage(connection, proof)
    retained = (
        table_names - {"users", "user_keys", "alembic_version"} - _DERIVED_TABLES - _APP_TABLES
    )
    before = _inventory(connection, retained)
    op.create_table(
        "soul_owners",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("core_owner_id", sa.String(36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("core_owner_id"),
    )
    op.create_table(
        "soul_schema_archives",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("source_revision", sa.String(32), nullable=False),
        sa.Column("source_inventory", sa.JSON(), nullable=False),
        sa.Column("provenance_state", sa.String(32), nullable=False),
        sa.Column("vacuum_completed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    if users:
        connection.execute(
            sa.text(
                "INSERT INTO soul_schema_archives (id,source_revision,source_inventory,provenance_state) "
                "VALUES (:id,:revision,:inventory,:state)"
            ),
            {
                "id": receipt.archive_id,
                "revision": down_revision,
                "inventory": json.dumps(
                    {
                        "combinedHash": receipt.inventory.combined_hash,
                        "tableHashes": dict(receipt.inventory.table_hashes),
                        "tableCounts": dict(receipt.inventory.table_counts),
                    },
                    sort_keys=True,
                ),
                "state": "authenticated" if proof.profile is not None else "source_unavailable",
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO soul_owners (id, core_owner_id, created_at, updated_at) "
                "VALUES (:id, :owner, :created, :updated)"
            ),
            {
                "id": users[0].id,
                "owner": owner_id,
                "created": users[0].created_at,
                "updated": users[0].updated_at,
            },
        )
    source_before = {}
    for name in sorted(retained):
        owner_fks = [
            fk for fk in inspector.get_foreign_keys(name) if fk["referred_table"] == "users"
        ]
        source_fks = [
            fk
            for fk in inspector.get_foreign_keys(name)
            if set(fk["constrained_columns"]) & set(_SOURCE_COLUMNS.get(name, {}))
        ]
        dropped_columns = _DERIVED_COLUMNS.get(name, set()) | set(_SOURCE_COLUMNS.get(name, {}))
        if name in _SOURCE_COLUMNS:
            projection = ", ".join(["id", *_SOURCE_COLUMNS[name]])
            source_before[name] = list(
                connection.exec_driver_sql(f'SELECT {projection} FROM "{name}"').mappings()
            )
        if owner_fks or dropped_columns:
            indexes = inspector.get_indexes(name)
            with op.batch_alter_table(name) as batch:
                for index in indexes:
                    if set(index["column_names"]) & dropped_columns:
                        batch.drop_index(index["name"])
                for fk in [*owner_fks, *source_fks]:
                    batch.drop_constraint(fk["name"], type_="foreignkey")
                for fk in owner_fks:
                    batch.create_foreign_key(
                        f"fk_{name}_{fk['constrained_columns'][0]}_soul_owners",
                        "soul_owners",
                        fk["constrained_columns"],
                        ["id"],
                        ondelete=fk["options"].get("ondelete"),
                    )
                for column in sorted(dropped_columns):
                    batch.drop_column(column)
                if name in _SOURCE_COLUMNS:
                    for destination, _family, many in _SOURCE_COLUMNS[name].values():
                        batch.add_column(
                            sa.Column(
                                destination, sa.JSON() if many else sa.String(255), nullable=True
                            )
                        )
                    batch.add_column(sa.Column("provenance_metadata", sa.JSON(), nullable=True))
                    if name == "foresight_signals":
                        batch.create_index(
                            "ix_foresight_signals_user_thread", ["user_id", "source_thread_uri"]
                        )
                    elif name == "memory_item_evidence":
                        batch.create_index(
                            "ix_memory_item_evidence_source_message", ["source_message_uri"]
                        )
                        batch.create_index(
                            "ix_memory_item_evidence_source_transcript", ["source_transcript_uri"]
                        )
        if name in _SOURCE_COLUMNS:
            from anima_server.services.corefs.soul_provenance import (
                current_source_proof,
                preserve_source_reference,
            )

            proof = current_source_proof(users[0].id) if users else None
            for row in source_before[name]:
                metadata = {"historicalSourceIds": {}, "unavailableSourceFields": []}
                updates = {}
                for field, (destination, family, many) in _SOURCE_COLUMNS[name].items():
                    value = row[field]
                    if many and isinstance(value, str):
                        value = json.loads(value)
                    uri, evidence = preserve_source_reference(
                        proof=proof,
                        field=field,
                        family=family,
                        value=value,
                        many=many,
                    )
                    metadata["historicalSourceIds"][field] = value
                    if evidence["provenanceState"] == "source_unavailable":
                        metadata["unavailableSourceFields"].append(field)
                    updates[destination] = json.dumps(uri) if many else uri
                metadata["provenanceState"] = (
                    "source_unavailable" if metadata["unavailableSourceFields"] else "authenticated"
                )
                updates["provenance_metadata"] = json.dumps(metadata, sort_keys=True)
                updates["id"] = row["id"]
                assignments = ", ".join(
                    f'"{column}" = :{column}' for column in updates if column != "id"
                )
                connection.execute(
                    sa.text(f'UPDATE "{name}" SET {assignments} WHERE id = :id'), updates
                )
                checked = json.loads(
                    connection.execute(
                        sa.text(f'SELECT provenance_metadata FROM "{name}" WHERE id = :id'),
                        {"id": row["id"]},
                    ).scalar_one()
                )
                if checked["historicalSourceIds"] != metadata["historicalSourceIds"]:
                    raise ValueError("Soul retirement changed original provenance evidence")
    # SQLite resolves trigger bodies during FK-enabled table drops, even when
    # the source tables are empty. Retire these historical dependencies before
    # removing their state table; the verified original retains their schema.
    for table in ("diary_folders", "diary_entries", "diary_attachments"):
        for operation in ("insert", "update", "delete", "update_old_user"):
            op.execute(
                sa.text(f"DROP TRIGGER IF EXISTS trg_corefs_writing_source_{table}_{operation}")
            )
    for name in sorted(_DERIVED_TABLES | _APP_TABLES):
        op.drop_table(name)
    op.drop_table("user_keys")
    op.drop_table("users")
    if _inventory(connection, retained) != before:
        raise ValueError("Soul retirement changed retained record counts or hashes")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Soul retirement left foreign-key violations")


def downgrade() -> None:
    raise ValueError("Soul retirement is forward-only; restore an authenticated Core backup")
