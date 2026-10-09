"""Unmapped numeric response compatibility over durable Core URI provenance."""

from __future__ import annotations

from sqlalchemy import case, func
from sqlalchemy.ext.hybrid import hybrid_property


def core_source_reference(
    uri_field: str, compatibility_field: str, family: str, *, many: bool = False
):
    def getter(instance):
        metadata = instance.provenance_metadata or {}
        return metadata.get("historicalSourceIds", {}).get(compatibility_field)

    def setter(instance, value):
        from anima_server.services.corefs.soul_provenance import (
            current_source_proof,
            preserve_live_source_reference,
        )

        proof = current_source_proof(getattr(instance, "user_id", None))
        uri, evidence = preserve_live_source_reference(
            proof=proof,
            user_id=getattr(instance, "user_id", None),
            field=compatibility_field,
            family=family,
            value=value,
            many=many,
        )
        current = dict(instance.provenance_metadata or {})
        source_ids = dict(current.get("historicalSourceIds", {}))
        source_ids.update(evidence["historicalSourceIds"])
        unavailable = set(current.get("unavailableSourceFields", []))
        if evidence["provenanceState"] == "source_unavailable":
            unavailable.add(compatibility_field)
        else:
            unavailable.discard(compatibility_field)
        current.update(
            historicalSourceIds=source_ids,
            provenanceState="source_unavailable" if unavailable else "authenticated",
            unavailableSourceFields=sorted(unavailable),
        )
        instance.provenance_metadata = current
        setattr(instance, uri_field, uri)

    def expression(cls):
        historical = func.json_extract(
            cls.provenance_metadata, f"$.historicalSourceIds.{compatibility_field}"
        )
        # Archived IDs remain factual evidence, but reusable Runtime primary
        # keys cannot authorize a join/deletion without authenticated source URI.
        if family == "message":
            if many:
                complete = func.json_array_length(
                    getattr(cls, uri_field)
                ) == func.json_array_length(historical)
                return case((complete, historical), else_=None)
            return case((getattr(cls, uri_field).is_not(None), historical), else_=None)
        return historical

    return hybrid_property(getter, setter).expression(expression)
