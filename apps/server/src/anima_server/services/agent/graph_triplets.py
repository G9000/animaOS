from __future__ import annotations

import logging
import re
from typing import Any

from anima_server.services import anima_core_bindings
from anima_server.services.agent.text_processing import prepare_memory_text

logger = logging.getLogger(__name__)

_rust_extract_triplets = anima_core_bindings.rust_extract_triplets


_FP_EMPLOYER_RE = re.compile(
    r"\b(?i:(?:i\s+work\s+(?:at|for)|i(?:'m|\s+am)\s+(?:at|with)|i(?:'m|\s+am)\s+employed\s+(?:at|by)))\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5})(?=\s*[.,;!?]|\s+(?i:as|and|where|since|for)\b|$)"
)
_FP_LOCATION_RE = re.compile(
    r"\b(?i:(?:i\s+live\s+in|i(?:'m|\s+am)\s+(?:from|in|based\s+in)|i\s+moved\s+to))\s+([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})(?=\s*[.,;!?]|\s+(?i:and|but|since|for|where)\b|$)"
)
_FP_INTEREST_RE = re.compile(
    r"\b(?i:(?:i\s+(?:love|like|enjoy|prefer)|i(?:'m|\s+am)\s+interested\s+in))\s+([A-Za-z][A-Za-z0-9&/'\- ]{1,40}?)(?=\s*[.,;!?]|\s+(?i:and|but|because|especially)\b|$)"
)
_FAMILY_RE = re.compile(
    r"\b(?i:my\s+(sister|brother|friend|wife|husband|spouse|coworker|colleague))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)
_NAMED_EMPLOYER_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:works\s+(?:at|for)|is\s+(?:at|with)|is\s+employed\s+(?:at|by))\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5})(?=\s*[.,;!?]|\s+(?:as|and|where|since|for)\b|$)"
)
_NAMED_LOCATION_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:lives\s+in|is\s+from|is\s+based\s+in|moved\s+to)\s+([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})(?=\s*[.,;!?]|\s+(?:and|but|since|for|where)\b|$)"
)
_NAMED_MARRIED_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+married\s+to\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)
_NAMED_INTEREST_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:likes|loves|enjoys|prefers)\s+([A-Za-z][A-Za-z0-9&/'\- ]{1,40}?)(?=\s*[.,;!?]|\s+(?:and|but|because|especially)\b|$)"
)

_FAMILY_RELATIONS = {
    "sister": "sister_of",
    "brother": "brother_of",
    "friend": "friend_of",
    "wife": "married_to",
    "husband": "married_to",
    "spouse": "married_to",
    "coworker": "colleague_of",
    "colleague": "colleague_of",
}


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n.,;:!?")


def _append_triplet(
    triplets: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    *,
    subject: str,
    subject_type: str,
    predicate: str,
    object_name: str,
    object_type: str,
    confidence: float,
    match: re.Match[str],
) -> None:
    clean_subject = _clean_value(subject)
    clean_object = _clean_value(object_name)
    if not clean_subject or not clean_object or not predicate:
        return

    key = (
        clean_subject.lower(),
        predicate.lower(),
        clean_object.lower(),
    )
    if key in seen:
        return
    seen.add(key)
    triplets.append(
        {
            "subject": clean_subject,
            "subject_type": subject_type,
            "predicate": predicate,
            "object": clean_object,
            "object_type": object_type,
            "confidence": float(confidence),
            "char_start": match.start(),
            "char_end": match.end(),
        }
    )


def _python_extract_triplets(text: str) -> list[dict[str, Any]]:
    triplets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for match in _FP_EMPLOYER_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject="User",
            subject_type="person",
            predicate="works_at",
            object_name=match.group(1),
            object_type="organization",
            confidence=0.95,
            match=match,
        )

    for match in _FP_LOCATION_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject="User",
            subject_type="person",
            predicate="lives_in",
            object_name=match.group(1),
            object_type="place",
            confidence=0.93,
            match=match,
        )

    for match in _FP_INTEREST_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject="User",
            subject_type="person",
            predicate="interested_in",
            object_name=match.group(1),
            object_type="concept",
            confidence=0.88,
            match=match,
        )

    for match in _FAMILY_RE.finditer(text):
        relation = _FAMILY_RELATIONS.get(match.group(1).lower())
        if relation is None:
            continue
        _append_triplet(
            triplets,
            seen,
            subject="User",
            subject_type="person",
            predicate=relation,
            object_name=match.group(2),
            object_type="person",
            confidence=0.9,
            match=match,
        )

    for match in _NAMED_EMPLOYER_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject=match.group(1),
            subject_type="person",
            predicate="works_at",
            object_name=match.group(2),
            object_type="organization",
            confidence=0.9,
            match=match,
        )

    for match in _NAMED_LOCATION_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject=match.group(1),
            subject_type="person",
            predicate="lives_in",
            object_name=match.group(2),
            object_type="place",
            confidence=0.9,
            match=match,
        )

    for match in _NAMED_MARRIED_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject=match.group(1),
            subject_type="person",
            predicate="married_to",
            object_name=match.group(2),
            object_type="person",
            confidence=0.92,
            match=match,
        )

    for match in _NAMED_INTEREST_RE.finditer(text):
        _append_triplet(
            triplets,
            seen,
            subject=match.group(1),
            subject_type="person",
            predicate="interested_in",
            object_name=match.group(2),
            object_type="concept",
            confidence=0.84,
            match=match,
        )

    return triplets


def _coerce_rust_triplet(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, (list, tuple)) or len(item) < 8:
        return None

    subject, subject_type, predicate, object_name, object_type, confidence, char_start, char_end = item[:8]
    clean_subject = _clean_value(str(subject))
    clean_object = _clean_value(str(object_name))
    clean_predicate = _clean_value(str(predicate)).lower()
    if not clean_subject or not clean_object or not clean_predicate:
        return None

    return {
        "subject": clean_subject,
        "subject_type": str(subject_type).lower() or "unknown",
        "predicate": clean_predicate,
        "object": clean_object,
        "object_type": str(object_type).lower() or "unknown",
        "confidence": float(confidence),
        "char_start": int(char_start),
        "char_end": int(char_end),
    }


def extract_triplets(text: str, *, limit: int = 8_192) -> list[dict[str, Any]]:
    prepared = prepare_memory_text(text, limit=limit, apply_pdf_spacing=False)
    if not prepared:
        return []

    if anima_core_bindings.rust_extract_triplets is not None:
        triplets: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in anima_core_bindings.rust_extract_triplets(prepared):
            triplet = _coerce_rust_triplet(item)
            if triplet is None:
                continue
            key = (
                triplet["subject"].lower(),
                triplet["predicate"].lower(),
                triplet["object"].lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            triplets.append(triplet)
        return triplets

    return _python_extract_triplets(prepared)
