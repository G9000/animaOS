from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SANITIZER_CONTRACT = json.loads(
    Path(__file__).with_name("writing-sanitizer-v1.json").read_text(encoding="utf-8")
)

DIARY_FORMAT_VERSION = 1
NOTE_FORMAT_VERSION = 1
DRAFT_FORMAT_VERSION = 1
DIARY_CONTENT_TYPE = "application/vnd.anima.diary+json;version=1"
NOTE_CONTENT_TYPE = "application/vnd.anima.note+json;version=1"
DRAFT_CONTENT_TYPE = "application/vnd.anima.draft+json;version=1"
WRITING_SANITIZER_CONTRACT = "anima-writing-html-v1"
MAX_INLINE_MEDIA_BYTES = int(_SANITIZER_CONTRACT["maxInlineMediaBytes"])
ALLOWED_INLINE_MEDIA_TYPES = frozenset(_SANITIZER_CONTRACT["allowedInlineMediaTypes"])
ALLOWED_DIARY_TAGS = frozenset(_SANITIZER_CONTRACT["allowedTags"])
ALLOWED_DIARY_ATTRIBUTES = frozenset(_SANITIZER_CONTRACT["allowedAttributes"])
_URI_POLICY = _SANITIZER_CONTRACT["uriPolicy"]
_ALLOWED_HREF_SCHEMES = frozenset(_URI_POLICY["allowedHrefSchemes"])
_ALLOWED_SRC_SCHEMES = frozenset(_URI_POLICY["allowedSrcSchemes"])
_ALLOW_RELATIVE_URIS = bool(_URI_POLICY["allowRelative"])
_ALLOW_SCHEME_RELATIVE_URIS = bool(_URI_POLICY["allowSchemeRelative"])
_CANONICAL_DATA_ACTION = _URI_POLICY["data"]["canonicalAction"]
_VOID_TAGS = frozenset({"br", "hr", "img"})
_DROP_CONTENT_TAGS = frozenset(_SANITIZER_CONTRACT["dropContentTags"])
_DATA_IMAGE_RE = re.compile(
    r"^data:(image/[a-z0-9.+-]+);base64,([a-zA-Z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
_OPAQUE_ID_RE = re.compile(r"[0-7][0-9A-HJKMNP-TV-Z]{25}")
_DATA_URL_ATTRIBUTE_RE = re.compile(
    r"\b(?:href|src)\s*=\s*(?:[\"']\s*)?data:",
    re.IGNORECASE,
)


class CoreFormatError(ValueError):
    """Raised when portable writing content cannot be made canonical."""


@dataclass(frozen=True, slots=True)
class CanonicalDiaryHtml:
    html: str
    media_uris: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiaryDocument:
    format_version: int
    stable_id: str
    entry_date: str
    title: str | None
    mood: str | None
    folder_id: str | None
    html: str
    cover_uri: str | None
    attachment_uris: tuple[str, ...]
    inline_media_uris: tuple[str, ...]
    legacy_id: int | None
    legacy_folder_id: int | None
    source: str | None
    created_at: str | None
    updated_at: str | None
    attachment_metadata: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class NoteDocument:
    format_version: int
    stable_id: str
    title: str | None
    content_type: str
    body: str


@dataclass(frozen=True, slots=True)
class DraftDocument:
    format_version: int
    stable_id: str
    target_id: str | None
    content_type: str
    body: str
    metadata: dict[str, Any]


MediaReferenceFactory = Callable[[str, bytes, str], str]


def canonicalize_diary_html(
    source: str,
    *,
    legacy_plain_text: bool = False,
    media_reference_factory: MediaReferenceFactory | None = None,
    max_inline_media_bytes: int = MAX_INLINE_MEDIA_BYTES,
) -> CanonicalDiaryHtml:
    if legacy_plain_text:
        source = "".join(
            f"<p>{html.escape(paragraph, quote=False)}</p>"
            for paragraph in re.split(r"(?:\r?\n){2,}", source)
            if paragraph
        )
    parser = _DiarySanitizer(max_inline_media_bytes=max_inline_media_bytes)
    try:
        parser.feed(source)
        parser.close()
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise CoreFormatError("Diary HTML contains malformed inline media.") from exc

    media_by_hash = parser.media_by_hash
    if media_by_hash and media_reference_factory is None:
        raise CoreFormatError("Inline diary media must be extracted before publication.")

    uris: dict[str, str] = {}
    for digest, (mime_type, data) in media_by_hash.items():
        assert media_reference_factory is not None
        uri = media_reference_factory(mime_type, data, digest)
        _validate_corefs_uri(uri)
        uris[digest] = uri

    sanitized = parser.render(uris)
    if _DATA_URL_ATTRIBUTE_RE.search(sanitized):
        raise CoreFormatError("Canonical diary HTML cannot contain data URLs.")
    return CanonicalDiaryHtml(html=sanitized, media_uris=tuple(uris.values()))


def encode_diary_document(
    *,
    stable_id: str,
    entry_date: str,
    title: str | None,
    mood: str | None,
    folder_id: str | None,
    html: str,
    cover_uri: str | None,
    attachment_uris: tuple[str, ...] = (),
    media_reference_factory: MediaReferenceFactory | None = None,
    legacy_plain_text: bool = False,
    legacy_id: int | None = None,
    legacy_folder_id: int | None = None,
    source: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    attachment_metadata: tuple[dict[str, Any], ...] = (),
) -> bytes:
    canonical = canonicalize_diary_html(
        html,
        legacy_plain_text=legacy_plain_text,
        media_reference_factory=media_reference_factory,
    )
    _validate_stable_id(stable_id)
    if folder_id is not None:
        _validate_stable_id(folder_id)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry_date):
        raise CoreFormatError("Diary entry date must use YYYY-MM-DD.")
    references = tuple(attachment_uris)
    if cover_uri is not None:
        _validate_corefs_uri(cover_uri)
    for uri in references:
        _validate_corefs_uri(uri)
    payload = {
        "format": "anima.diary",
        "version": DIARY_FORMAT_VERSION,
        "stableId": stable_id,
        "entryDate": entry_date,
        "title": title,
        "mood": mood,
        "folderId": folder_id,
        "html": canonical.html,
        "coverUri": cover_uri,
        "attachmentUris": list(references),
        "inlineMediaUris": list(canonical.media_uris),
        "legacyId": legacy_id,
        "legacyFolderId": legacy_folder_id,
        "source": source,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "attachments": list(attachment_metadata),
        "sanitizer": WRITING_SANITIZER_CONTRACT,
    }
    return _canonical_json(payload)


def decode_diary_document(data: bytes) -> DiaryDocument:
    payload = _decode_object(data, expected_format="anima.diary", version=DIARY_FORMAT_VERSION)
    stable_id = _required_string(payload, "stableId")
    _validate_stable_id(stable_id)
    html_body = _required_string(payload, "html", allow_empty=True)
    if _DATA_URL_ATTRIBUTE_RE.search(html_body):
        raise CoreFormatError("Canonical diary HTML cannot contain data URLs.")
    attachment_uris = _string_tuple(payload, "attachmentUris")
    inline_media_uris = _string_tuple(payload, "inlineMediaUris")
    cover_uri = _optional_string(payload, "coverUri")
    for uri in (*attachment_uris, *inline_media_uris):
        _validate_corefs_uri(uri)
    if cover_uri is not None:
        _validate_corefs_uri(cover_uri)
    return DiaryDocument(
        format_version=DIARY_FORMAT_VERSION,
        stable_id=stable_id,
        entry_date=_required_string(payload, "entryDate"),
        title=_optional_string(payload, "title"),
        mood=_optional_string(payload, "mood"),
        folder_id=_optional_string(payload, "folderId"),
        html=html_body,
        cover_uri=cover_uri,
        attachment_uris=attachment_uris,
        inline_media_uris=inline_media_uris,
        legacy_id=_optional_int(payload, "legacyId"),
        legacy_folder_id=_optional_int(payload, "legacyFolderId"),
        source=_optional_string(payload, "source"),
        created_at=_optional_string(payload, "createdAt"),
        updated_at=_optional_string(payload, "updatedAt"),
        attachment_metadata=_object_tuple(payload, "attachments"),
    )


def encode_note_document(
    *, stable_id: str, title: str | None, content_type: str, body: str
) -> bytes:
    _validate_stable_id(stable_id)
    if content_type == "text/html":
        body = canonicalize_diary_html(body).html
    elif content_type != "text/markdown":
        raise CoreFormatError("Notes must use Markdown or sanitized HTML.")
    payload = {
        "format": "anima.note",
        "version": NOTE_FORMAT_VERSION,
        "stableId": stable_id,
        "title": title,
        "contentType": content_type,
        "body": body,
        "sanitizer": WRITING_SANITIZER_CONTRACT,
    }
    return _canonical_json(payload)


def decode_note_document(data: bytes) -> NoteDocument:
    payload = _decode_object(data, expected_format="anima.note", version=NOTE_FORMAT_VERSION)
    content_type = _required_string(payload, "contentType")
    if content_type not in {"text/html", "text/markdown"}:
        raise CoreFormatError("Notes must use Markdown or sanitized HTML.")
    stable_id = _required_string(payload, "stableId")
    _validate_stable_id(stable_id)
    return NoteDocument(
        format_version=NOTE_FORMAT_VERSION,
        stable_id=stable_id,
        title=_optional_string(payload, "title"),
        content_type=content_type,
        body=_required_string(payload, "body", allow_empty=True),
    )


def encode_draft_document(
    *,
    stable_id: str,
    target_id: str | None,
    content_type: str,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> bytes:
    _validate_stable_id(stable_id)
    if target_id is not None:
        _validate_stable_id(target_id)
    if content_type == "text/html":
        body = canonicalize_diary_html(body).html
    elif content_type != "text/markdown":
        raise CoreFormatError("Drafts must use Markdown or sanitized HTML.")
    return _canonical_json(
        {
            "format": "anima.draft",
            "version": DRAFT_FORMAT_VERSION,
            "stableId": stable_id,
            "targetId": target_id,
            "contentType": content_type,
            "body": body,
            "metadata": metadata or {},
            "sanitizer": WRITING_SANITIZER_CONTRACT,
        }
    )


def decode_draft_document(data: bytes) -> DraftDocument:
    payload = _decode_object(data, expected_format="anima.draft", version=DRAFT_FORMAT_VERSION)
    stable_id = _required_string(payload, "stableId")
    _validate_stable_id(stable_id)
    target_id = _optional_string(payload, "targetId")
    if target_id is not None:
        _validate_stable_id(target_id)
    content_type = _required_string(payload, "contentType")
    if content_type not in {"text/html", "text/markdown"}:
        raise CoreFormatError("Drafts must use Markdown or sanitized HTML.")
    return DraftDocument(
        format_version=DRAFT_FORMAT_VERSION,
        stable_id=stable_id,
        target_id=target_id,
        content_type=content_type,
        body=_required_string(payload, "body", allow_empty=True),
        metadata=_object(payload, "metadata"),
    )


class _DiarySanitizer(HTMLParser):
    def __init__(self, *, max_inline_media_bytes: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_inline_media_bytes = max_inline_media_bytes
        self.parts: list[str] = []
        self.media_by_hash: dict[str, tuple[str, bytes]] = {}
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or tag not in ALLOWED_DIARY_TAGS:
            return
        clean_attrs: list[tuple[str, str]] = []
        for name, value in attrs:
            name = name.lower()
            if name not in ALLOWED_DIARY_ATTRIBUTES or value is None:
                continue
            if name in {"href", "src"}:
                value = self._safe_url(tag, name, value)
                if value is None:
                    continue
            if name == "target" and value not in {"_blank", "_self"}:
                continue
            clean_attrs.append((name, value))
        rendered_attrs = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in clean_attrs
        )
        self.parts.append(f"<{tag}{rendered_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if not self._drop_depth and tag in ALLOWED_DIARY_TAGS and tag not in _VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._drop_depth:
            self.parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._drop_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._drop_depth:
            self.parts.append(f"&#{name};")

    def _safe_url(self, tag: str, name: str, value: str) -> str | None:
        normalized = value.strip()
        parsed = urlsplit(normalized)
        scheme = parsed.scheme.lower()
        if (
            scheme == "data"
            and _CANONICAL_DATA_ACTION == "extract-supported-image"
            and name == "src"
            and tag == "img"
        ):
            match = _DATA_IMAGE_RE.fullmatch(normalized)
            if match is None:
                raise CoreFormatError("Malformed inline diary image.")
            mime_type = match.group(1).lower()
            if mime_type not in ALLOWED_INLINE_MEDIA_TYPES:
                raise CoreFormatError("Unsupported inline diary image type.")
            data = base64.b64decode(match.group(2), validate=True)
            if not data or len(data) > self.max_inline_media_bytes:
                raise CoreFormatError("Inline diary image exceeds the allowed size.")
            digest = hashlib.sha256(data).hexdigest()
            self.media_by_hash.setdefault(digest, (mime_type, data))
            return f"__ANIMA_INLINE_MEDIA_{digest}__"
        if scheme == "data":
            raise CoreFormatError("Data URLs are only supported for inline diary images.")
        allowed_schemes = _ALLOWED_HREF_SCHEMES if name == "href" else _ALLOWED_SRC_SCHEMES
        if scheme in allowed_schemes:
            return normalized
        if not scheme:
            if normalized.startswith("//"):
                return normalized if _ALLOW_SCHEME_RELATIVE_URIS else None
            return normalized if _ALLOW_RELATIVE_URIS else None
        return None

    def render(self, uris: dict[str, str]) -> str:
        output = "".join(self.parts)
        for digest, uri in uris.items():
            output = output.replace(f"__ANIMA_INLINE_MEDIA_{digest}__", uri)
        return output


def _validate_corefs_uri(value: str) -> None:
    parsed = urlsplit(value)
    stable_id = parsed.path.removeprefix("/")
    if (
        parsed.scheme != "corefs"
        or parsed.netloc != "object"
        or "/" in stable_id
        or _OPAQUE_ID_RE.fullmatch(stable_id) is None
        or parsed.query
        or parsed.fragment
    ):
        raise CoreFormatError("Portable media references must be stable CoreFS URIs.")


def _validate_stable_id(value: str) -> None:
    if _OPAQUE_ID_RE.fullmatch(value) is None:
        raise CoreFormatError("Portable writing objects require a native opaque ID.")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _decode_object(data: bytes, *, expected_format: str, version: int) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoreFormatError("Portable writing object is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise CoreFormatError("Portable writing object must be a JSON object.")
    if payload.get("format") != expected_format or payload.get("version") != version:
        raise CoreFormatError("Unsupported portable writing format version.")
    return payload


def _required_string(payload: dict[str, Any], key: str, *, allow_empty: bool = False) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CoreFormatError(f"Portable writing field {key} must be a string.")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CoreFormatError(f"Portable writing field {key} must be a string or null.")
    return value


def _string_tuple(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CoreFormatError(f"Portable writing field {key} must be a string list.")
    return tuple(value)


def _object_tuple(payload: dict[str, Any], key: str) -> tuple[dict[str, Any], ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CoreFormatError(f"Portable writing field {key} must be an object list.")
    return tuple(value)


def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key, {})
    if not isinstance(value, dict):
        raise CoreFormatError(f"Portable writing field {key} must be an object.")
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise CoreFormatError(f"Portable writing field {key} must be an integer or null.")
    return value
