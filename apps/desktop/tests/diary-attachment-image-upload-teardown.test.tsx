import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Real jsdom + react-dom/client mount (not renderToStaticMarkup) — same
// pattern used throughout this suite (diary-attachment-blob-url.test.tsx,
// diary-upload-graduation.test.tsx): the bug this file guards against only
// reproduces once a real React NodeView actually mounts, unmounts, and a
// promise resolves around that teardown — none of that exists under
// renderToStaticMarkup or a hand-rolled stub.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://localhost/",
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).Text = dom.window.Text;
(globalThis as any).DocumentFragment = dom.window.DocumentFragment;
(globalThis as any).customElements = dom.window.customElements;
(globalThis as any).getSelection = () => dom.window.getSelection();
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
// @tiptap/react's selection-sync effect schedules via rAF; jsdom doesn't
// implement it.
(globalThis as any).requestAnimationFrame = (cb: FrameRequestCallback) => setTimeout(() => cb(Date.now()), 0);
(globalThis as any).cancelAnimationFrame = (id: any) => clearTimeout(id);

const React = await import("react");
const { useEffect, useRef } = React;
const { createRoot } = await import("react-dom/client");
const { useEditor, EditorContent } = await import("@tiptap/react");
const StarterKit = (await import("@tiptap/starter-kit")).default;

// The REAL, currently-shipped module (this file's fix target).
const AfterFix = await import("../src/features/diary/editor/nodes/AttachmentImage");

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function file(name: string): File {
  return new File(["x"], name, { type: "image/png" });
}

async function mount(element: any): Promise<{ unmount: () => void }> {
  // A fresh container per mount (rather than reusing the shared #root)
  // avoids react-dom's "createRoot() on a container that has already been
  // passed to createRoot()" warning across this file's several tests.
  const container = dom.window.document.createElement("div");
  dom.window.document.body.appendChild(container);
  const root = createRoot(container);
  const { act } = React;
  await act(async () => {
    root.render(element);
  });
  return {
    unmount: () => {
      // Caller wraps this in `act` — kept as a plain call so both
      // sync-unmount-only and act-wrapped call sites work.
      root.unmount();
    },
  };
}

// Minimal editor harness: StarterKit for Document/Paragraph/Text (needed
// for a valid schema) plus whichever DiaryImage build the test wires in.
function Harness({
  diaryImageExtension,
  onReady,
}: {
  diaryImageExtension: any;
  onReady: (editor: any) => void;
}) {
  const editor = useEditor({
    extensions: [StarterKit.configure({ codeBlock: false }), diaryImageExtension],
    content: "<p></p>",
  });
  const reportedRef = useRef(false);
  useEffect(() => {
    if (editor && !reportedRef.current) {
      reportedRef.current = true;
      onReady(editor);
    }
  }, [editor, onReady]);
  return editor ? React.createElement(EditorContent, { editor }) : null;
}

function firstDiaryImageLocalId(editor: any): string {
  let found: string | null = null;
  editor.state.doc.descendants((node: any) => {
    if (node.type.name === "diaryImage" && node.attrs.localId) {
      found = node.attrs.localId;
      return false;
    }
    return true;
  });
  if (!found) throw new Error("expected a diaryImage node in the document");
  return found;
}

const { act } = React;

describe("PR #139 round 5 (P1): inline-image upload completion must survive NodeView teardown", () => {
  test("AFTER (fixed): a successful upload that resolves after the editor unmounts cleans up pendingFiles and reports the orphan instead of silently discarding it", async () => {
    const upload = deferred<number | null>();
    let editorRef: any;
    let orphaned: [number, number] | null = null;

    const DiaryImageAfter = AfterFix.DiaryImage.configure({
      entryId: 7,
      uploadImage: () => upload.promise,
      onUploadOrphaned: (entryId: number, attachmentId: number) => {
        orphaned = [entryId, attachmentId];
      },
    });

    const handle = await mount(
      React.createElement(Harness, {
        diaryImageExtension: DiaryImageAfter,
        onReady: (ed: any) => {
          editorRef = ed;
        },
      }),
    );

    await act(async () => {
      editorRef.commands.insertAttachmentImage(file("photo.png"));
    });
    const localId = firstDiaryImageLocalId(editorRef);
    const storage = editorRef.storage.diaryImage;
    expect(storage.pendingFiles.has(localId)).toBe(true);

    await act(async () => {
      handle.unmount();
    });

    upload.resolve(42);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    // FIXED: cleaned up, not leaked.
    expect(storage.pendingFiles.has(localId)).toBe(false);
    // FIXED: the caller is told, with the ORIGINATING entry id (7, from
    // this options object — not whatever entry might be open now) and the
    // real attachment id the server created.
    expect(orphaned).toEqual([7, 42]);
  });

  test("mounted happy path is unchanged: a successful upload still writes attachmentId + uploadState:'ready' inline while the editor is still mounted", async () => {
    const upload = deferred<number | null>();
    let editorRef: any;
    let orphaned = false;

    const DiaryImageAfter = AfterFix.DiaryImage.configure({
      entryId: 7,
      uploadImage: () => upload.promise,
      onUploadOrphaned: () => {
        orphaned = true;
      },
    });

    const handle = await mount(
      React.createElement(Harness, {
        diaryImageExtension: DiaryImageAfter,
        onReady: (ed: any) => {
          editorRef = ed;
        },
      }),
    );

    try {
      await act(async () => {
        editorRef.commands.insertAttachmentImage(file("photo.png"));
      });

      upload.resolve(99);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      const node = (() => {
        let found: any = null;
        editorRef.state.doc.descendants((n: any) => {
          if (n.type.name === "diaryImage") {
            found = n;
            return false;
          }
          return true;
        });
        return found;
      })();

      expect(node).not.toBeNull();
      expect(node.attrs.attachmentId).toBe(99);
      expect(node.attrs.uploadState).toBe("ready");
      expect(orphaned).toBe(false);
    } finally {
      await act(async () => {
        handle.unmount();
      });
    }
  });
});

describe("PR #139 round 5: isEditorAvailable / handleUploadResolution (pure, unit-testable)", () => {
  test("isEditorAvailable reflects whether a live handler is registered for that localId", () => {
    const storage = { pendingFiles: new Map(), liveHandlers: new Map() } as any;
    expect(AfterFix.isEditorAvailable(storage, "a")).toBe(false);
    storage.liveHandlers.set("a", () => {});
    expect(AfterFix.isEditorAvailable(storage, "a")).toBe(true);
  });

  test("handleUploadResolution forwards to the live handler and leaves pendingFiles for the caller when mounted", () => {
    const storage = { pendingFiles: new Map([["a", file("x.png")]]), liveHandlers: new Map() } as any;
    const outcomes: any[] = [];
    storage.liveHandlers.set("a", (outcome: any) => outcomes.push(outcome));
    const orphaned: number[] = [];

    AfterFix.handleUploadResolution(storage, "a", 5, (id: number) => orphaned.push(id));

    expect(outcomes).toEqual([{ status: "ready", attachmentId: 5 }]);
    expect(storage.pendingFiles.has("a")).toBe(false);
    expect(orphaned).toEqual([]);
  });

  test("handleUploadResolution cleans up pendingFiles and reports the orphan when not mounted", () => {
    const storage = { pendingFiles: new Map([["a", file("x.png")]]), liveHandlers: new Map() } as any;
    const orphaned: number[] = [];

    AfterFix.handleUploadResolution(storage, "a", 5, (id: number) => orphaned.push(id));

    expect(storage.pendingFiles.has("a")).toBe(false);
    expect(orphaned).toEqual([5]);
  });

  test("handleUploadResolution on failure with no live handler does nothing (accepted limitation: a discarded FAILURE with no server-side trace, unlike a discarded success)", () => {
    const storage = { pendingFiles: new Map([["a", file("x.png")]]), liveHandlers: new Map() } as any;
    const orphaned: number[] = [];

    AfterFix.handleUploadResolution(storage, "a", null, (id: number) => orphaned.push(id));

    expect(orphaned).toEqual([]);
    // Failure path never touches pendingFiles either way — Retry needs it.
    expect(storage.pendingFiles.has("a")).toBe(true);
  });
});
