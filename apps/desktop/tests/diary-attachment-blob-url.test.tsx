import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// A real jsdom + react-dom/client mount, not renderToStaticMarkup — this
// bug (fix round 1, Finding 1) only reproduces once effects actually run
// and a component actually re-renders, which static markup never does.
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: "http://localhost/",
});
(globalThis as any).window = dom.window;
(globalThis as any).document = dom.window.document;
(globalThis as any).navigator = dom.window.navigator;
(globalThis as any).HTMLElement = dom.window.HTMLElement;
(globalThis as any).Node = dom.window.Node;
(globalThis as any).customElements = dom.window.customElements;
// jsdom does not implement the Blob URL registry; useAttachmentBlobUrl
// calls both of these on every successful fetch / every cleanup.
(dom.window as any).URL.createObjectURL = () => "blob:mock-url";
(dom.window as any).URL.revokeObjectURL = () => {};
(globalThis as any).URL = dom.window.URL;
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const { api } = await import("../src/lib/api");
const { useAttachmentBlobUrl } = await import(
  "../src/features/diary/hooks/useAttachmentBlobUrl"
);

describe("useAttachmentBlobUrl (fix round 1, Finding 1: unstable onError)", () => {
  test("does not re-fetch in a loop when the caller passes a fresh onError callback every render", async () => {
    let callCount = 0;
    const originalDownload = api.diary.downloadAttachment;
    (api.diary as any).downloadAttachment = async () => {
      callCount += 1;
      return new Blob(["x"], { type: "image/png" });
    };

    // Deliberately the exact shape every real caller in this codebase
    // uses (AttachmentImageView, DetailsDrawer, LibrarySidebar): a brand
    // new arrow function passed as `onError` on every render, never
    // memoized by the caller.
    function Harness() {
      useAttachmentBlobUrl({ entryId: 1, id: 2 }, () => {});
      return null;
    }

    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);

    try {
      await act(async () => {
        root.render(<Harness />);
      });
      // The original bug produced 16k+ fetches in 300ms against an
      // instantly-resolving stub (setUrl -> re-render -> new onError
      // identity -> effect cleanup revokes the object URL -> effect
      // re-runs -> fetch again, forever). Give a runaway loop plenty of
      // room to spiral before asserting.
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 200));
      });
    } finally {
      root.unmount();
      (api.diary as any).downloadAttachment = originalDownload;
    }

    expect(callCount).toBe(1);
  });
});
