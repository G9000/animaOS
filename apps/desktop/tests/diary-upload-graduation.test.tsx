import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Real jsdom + react-dom/client mount (not renderToStaticMarkup) — same
// pattern used throughout this suite (diary-attachment-blob-url.test.tsx,
// diary-voice-recorder-entry-capture.test.tsx): these hooks have real
// effects and real async ordering that only reproduce with a real mount.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://localhost/",
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).customElements = dom.window.customElements;
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  state = "inactive";
  mimeType = "audio/webm";
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(_stream: unknown, _options?: unknown) {
    FakeMediaRecorder.instances.push(this);
  }
  start(_timesliceMs?: number) {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.onstop?.();
  }
}
(globalThis as any).MediaRecorder = FakeMediaRecorder;
(globalThis as any).SpeechRecognition = undefined;

const React = await import("react");
const { act, useRef } = React;
const { createRoot } = await import("react-dom/client");
const { api } = await import("../src/lib/api");
const { useDiaryEntries } = await import("../src/features/diary/hooks/useDiaryEntries");
const { useVoiceRecorder } = await import("../src/features/diary/hooks/useVoiceRecorder");
const { isSessionDiscardable, graduateSessionEntry } = await import(
  "../src/features/diary/lib/pageLifecycle"
);

function file(name: string, type: string): File {
  return new File(["x"], name, { type });
}

// Same "blank, session-created entry" shape used throughout
// diary-session-discard.test.tsx's `base` fixture, minus createdThisSession
// (supplied per-assertion below).
const blankEntry = {
  title: null,
  bodyPlainText: "",
  attachmentCount: 0,
  coverAttachmentId: null,
  hasNonTextContent: false,
  mood: null,
  folderId: null,
  initialFolderId: null,
  entryDate: "2026-01-01",
  initialEntryDate: "2026-01-01",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function mount(element: any): Promise<{ unmount: () => void }> {
  const container = dom.window.document.getElementById("root") as HTMLElement;
  const root = createRoot(container);
  await act(async () => {
    root.render(element);
  });
  return { unmount: () => root.unmount() };
}

// ---------------------------------------------------------------------------
// FINDING (P1): "attachment upload races the discard check" — an attachment
// upload initiated against a session-created entry must graduate that entry
// out of discard eligibility the instant it is INITIATED, not whenever the
// network call happens to resolve.
// ---------------------------------------------------------------------------
describe("PR #139 round 4: attachment-upload initiation graduates synchronously", () => {
  test("BEFORE (bug reproduction, real shipped uploadAttachment): the pre-round-4 call shape — useDiaryEntries(userId) with no onUploadInitiated wired — leaves a session-created entry discardable for the entire duration an upload is in flight", async () => {
    const originalList = api.diary.list;
    const originalFolders = api.diary.folders.list;
    const originalUpload = api.diary.uploadAttachment;
    const upload = deferred<any>();
    (api.diary as any).list = async () => [];
    (api.diary.folders as any).list = async () => [];
    (api.diary as any).uploadAttachment = () => upload.promise;

    const sessionCreatedIds = new Set<number>([1]);
    let uploadAttachmentFn!: (entryId: number, file: File) => Promise<any>;

    function Harness() {
      // This is the literal shape every call site used before round 4:
      // `useDiaryEntries(user?.id ?? null)` — no second argument, so the
      // hook's real, unchanged uploadAttachment has nothing to call at
      // initiation time.
      const { uploadAttachment } = useDiaryEntries(1);
      uploadAttachmentFn = uploadAttachment;
      return null;
    }

    const handle = await mount(<Harness />);
    try {
      // Initiate the upload but do NOT await it — this is the exact
      // moment the reported race lives in: the network call is in flight,
      // entry.attachments has not been updated, and (pre-fix) nothing has
      // told the session-eligibility set that real work has started.
      let uploadPromise: Promise<any>;
      await act(async () => {
        uploadPromise = uploadAttachmentFn(1, file("photo.png", "image/png"));
      });

      // BUG: the entry is still fully discardable while the upload is
      // in flight — a discard evaluation firing right now (the user
      // switching entries immediately after clicking Attach) would
      // delete it out from under the upload.
      expect(
        isSessionDiscardable({
          ...blankEntry,
          createdThisSession: sessionCreatedIds.has(1),
        }),
      ).toBe(true); // BUG: should already be false the instant upload was initiated

      upload.resolve({ id: 9, entryId: 1, kind: "file", filename: "photo.png" });
      await act(async () => {
        await uploadPromise!;
      });

      // And it is STILL discardable even after the upload completed,
      // because nothing in the pre-fix call shape ever removes the id —
      // graduation only happened on a real body/title/drawer edit, never
      // on an attachment upload.
      expect(
        isSessionDiscardable({
          ...blankEntry,
          createdThisSession: sessionCreatedIds.has(1),
        }),
      ).toBe(true); // BUG persists post-completion too, under this call shape
    } finally {
      await act(async () => {
        handle.unmount();
      });
      (api.diary as any).list = originalList;
      (api.diary.folders as any).list = originalFolders;
      (api.diary as any).uploadAttachment = originalUpload;
    }
  });

  test("AFTER (fixed): wiring onUploadInitiated (as DiaryWorkspace.tsx now does) graduates the entry synchronously, before the upload ever resolves", async () => {
    const originalList = api.diary.list;
    const originalFolders = api.diary.folders.list;
    const originalUpload = api.diary.uploadAttachment;
    const upload = deferred<any>();
    (api.diary as any).list = async () => [];
    (api.diary.folders as any).list = async () => [];
    (api.diary as any).uploadAttachment = () => upload.promise;

    const sessionCreatedIds = new Set<number>([1]);
    let uploadAttachmentFn!: (entryId: number, file: File) => Promise<any>;

    function Harness() {
      const { uploadAttachment } = useDiaryEntries(1, {
        onUploadInitiated: (entryId) => graduateSessionEntry(sessionCreatedIds, entryId),
      });
      uploadAttachmentFn = uploadAttachment;
      return null;
    }

    const handle = await mount(<Harness />);
    try {
      let uploadPromise: Promise<any>;
      await act(async () => {
        uploadPromise = uploadAttachmentFn(1, file("photo.png", "image/png"));
      });

      // FIXED: graduated already — before the network call has resolved
      // at all (`upload.resolve` has not even been called yet below).
      expect(
        isSessionDiscardable({
          ...blankEntry,
          createdThisSession: sessionCreatedIds.has(1),
        }),
      ).toBe(false);
      expect(sessionCreatedIds.has(1)).toBe(false);

      upload.resolve({ id: 9, entryId: 1, kind: "file", filename: "photo.png" });
      await act(async () => {
        await uploadPromise!;
      });

      // Still graduated (permanent) after completion too.
      expect(sessionCreatedIds.has(1)).toBe(false);
    } finally {
      await act(async () => {
        handle.unmount();
      });
      (api.diary as any).list = originalList;
      (api.diary.folders as any).list = originalFolders;
      (api.diary as any).uploadAttachment = originalUpload;
    }
  });
});

// ---------------------------------------------------------------------------
// FINDING (P1): "voice-only entry deleted before its audio lands" — starting
// a recording against a session-created entry must graduate it the instant
// recording is INITIATED (before getUserMedia even resolves), not whenever
// the recording completes.
// ---------------------------------------------------------------------------
describe("PR #139 round 4: recording initiation graduates synchronously", () => {
  test("BEFORE (bug reproduction, real shipped useVoiceRecorder): the pre-round-4 options shape — no onRecordingInitiated — leaves a session-created entry discardable while getUserMedia is still pending", async () => {
    const media = deferred<{ getTracks: () => { stop: () => void }[] }>();
    (dom.window.navigator as any).mediaDevices = {
      getUserMedia: () => media.promise,
    };

    const sessionCreatedIds = new Set<number>([1]);
    let startFn!: (entryId: number) => Promise<void>;

    function Harness() {
      // The literal pre-round-4 options shape: no onRecordingInitiated at
      // all (it did not exist yet), matching every real call site before
      // this fix.
      const recorder = useVoiceRecorder({
        onFinalTranscript: () => {},
        onRecordingComplete: () => {},
        onError: () => {},
      });
      startFn = recorder.start;
      return null;
    }

    const handle = await mount(<Harness />);
    try {
      // Click "record" — start() begins running but getUserMedia (the
      // permission prompt) has not resolved yet.
      let startPromise: Promise<void>;
      await act(async () => {
        startPromise = startFn(1);
      });

      // BUG: still fully discardable — a discard evaluation firing right
      // now (navigating away while the permission prompt is up) would
      // delete the entry before any audio exists.
      expect(
        isSessionDiscardable({
          ...blankEntry,
          createdThisSession: sessionCreatedIds.has(1),
        }),
      ).toBe(true); // BUG: should already be false the instant recording was initiated

      media.resolve({ getTracks: () => [{ stop: () => {} }] });
      await act(async () => {
        await startPromise!;
      });
    } finally {
      await act(async () => {
        handle.unmount();
      });
      (dom.window.navigator as any).mediaDevices = undefined;
    }
  });

  test("AFTER (fixed): wiring onRecordingInitiated (as DiaryWorkspace.tsx now does) graduates the entry synchronously, before getUserMedia resolves", async () => {
    const media = deferred<{ getTracks: () => { stop: () => void }[] }>();
    (dom.window.navigator as any).mediaDevices = {
      getUserMedia: () => media.promise,
    };

    const sessionCreatedIds = new Set<number>([1]);
    let startFn!: (entryId: number) => Promise<void>;

    function Harness() {
      const recorder = useVoiceRecorder({
        onFinalTranscript: () => {},
        onRecordingComplete: () => {},
        onError: () => {},
        onRecordingInitiated: (entryId) => graduateSessionEntry(sessionCreatedIds, entryId),
      });
      startFn = recorder.start;
      return null;
    }

    const handle = await mount(<Harness />);
    try {
      let startPromise: Promise<void>;
      await act(async () => {
        startPromise = startFn(1);
      });

      // FIXED: graduated already, before getUserMedia has resolved at all
      // (media.resolve has not even been called yet below).
      expect(
        isSessionDiscardable({
          ...blankEntry,
          createdThisSession: sessionCreatedIds.has(1),
        }),
      ).toBe(false);
      expect(sessionCreatedIds.has(1)).toBe(false);

      media.resolve({ getTracks: () => [{ stop: () => {} }] });
      await act(async () => {
        await startPromise!;
      });

      expect(sessionCreatedIds.has(1)).toBe(false);
    } finally {
      await act(async () => {
        handle.unmount();
      });
      (dom.window.navigator as any).mediaDevices = undefined;
    }
  });
});
