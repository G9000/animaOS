# Daily Diary Design

## Context

The current `Journal` desktop page displays generated `memory_episodes`. Those are Anima-authored summaries of conversations, not user-authored daily diary/log entries. The old `memory_daily_logs` storage path has been removed, so diary entries need their own first-class model instead of overloading episodes or runtime messages.

## Approved Direction

Build a private daily diary that accepts text plus file, voice, and video uploads. Version 1 stores and displays entries, supports browser-side voice recording, and can use browser speech recognition to draft text when available. It does not run server-side transcription, extract memories, or run media analysis.

## Architecture

Diary records live in the encrypted per-user Core database because they are enduring personal life records. Attachment bytes live under `.anima/dev/users/{user_id}/diary/attachments/` as AES-GCM encrypted blobs using the existing memories-domain DEK. The original source file remains outside Anima; Anima stores its own encrypted copy.

Two new Core tables are added:

- `diary_entries`: one user-authored entry with `entry_date`, encrypted `title`, encrypted `body`, optional encrypted `mood`, and timestamps.
- `diary_attachments`: metadata and encrypted blob reference for one attachment. Original filenames and captions are encrypted. MIME type, media kind, byte size, checksum, and storage path remain queryable operational metadata.

## API

Add `/api/diary` endpoints:

- `GET /api/diary?userId=...&limit=...` lists entries with attachments.
- `POST /api/diary` creates a text entry.
- `POST /api/diary/{entry_id}/attachments` accepts multipart uploads for image, audio, video, or generic file.
- `GET /api/diary/{entry_id}/attachments/{attachment_id}` streams a decrypted attachment to the unlocked owner.
- `DELETE /api/diary/{entry_id}` deletes the row and encrypted blobs for that entry.

All endpoints require the active unlock session and reject cross-user access.

## UI

Replace the existing Journal page with a practical diary workspace:

- composer for date, title, body, optional mood, and attachments;
- mic control for recording audio directly in the composer;
- browser speech recognition appends recognized text into the encrypted diary body when the runtime supports it;
- timeline of recent entries;
- attachment chips with media kind, MIME type, and size;
- links to generated episode memories remain visible as "Anima memories" so the old journal surface is not lost.

## Privacy Invariants

- Diary title/body/mood/caption/original filename are encrypted before persistence.
- Attachment bytes are encrypted before being written under `.anima/`.
- Attachment download decrypts only after the unlock session is verified.
- Recorded audio is stored as an encrypted attachment even if transcription is unavailable.
- Browser speech recognition is opportunistic UI-side transcription; no server-side transcription or memory promotion happens in v1.

## Testing

Backend tests cover:

- creating and listing an encrypted diary entry;
- uploading an attachment and verifying the stored file is not plaintext;
- downloading the attachment only as the owning unlocked user.

Frontend verification is via TypeScript/build after wiring the API client and Journal page.
