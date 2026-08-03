import { describe, expect, test } from "bun:test";
import { JSDOM } from "jsdom";

// Same real jsdom + react-dom/client mount pattern used elsewhere in this
// suite (see diary-attachment-blob-url.test.tsx) — needed because
// useVoiceRecorder is a real hook with real effects, not something that
// can be exercised via renderToStaticMarkup.
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

// jsdom has no MediaRecorder / SpeechRecognition / getUserMedia — fake
// just enough of each to drive useVoiceRecorder's real start/onstop/
// onresult machinery, and to control exactly WHEN each fires (the whole
// point: these are asynchronous relative to whatever entry is selected by
// the time they run).
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
    this.ondataavailable?.({ data: new Blob(["chunk"]) });
  }
  stop() {
    this.state = "inactive";
    this.onstop?.();
  }
}

class FakeSpeechRecognition {
  static instances: FakeSpeechRecognition[] = [];
  continuous = false;
  interimResults = false;
  lang = "";
  onresult: ((event: any) => void) | null = null;
  onerror: (() => void) | null = null;
  onend: (() => void) | null = null;
  constructor() {
    FakeSpeechRecognition.instances.push(this);
  }
  start() {}
  stop() {}
  abort() {}
}

function fakeResult(transcript: string, isFinal: boolean) {
  return { isFinal, length: 1, 0: { transcript } };
}

(globalThis as any).MediaRecorder = FakeMediaRecorder;
(globalThis as any).SpeechRecognition = FakeSpeechRecognition;
(dom.window.navigator as any).mediaDevices = {
  getUserMedia: async () => ({
    getTracks: () => [{ stop: () => {} }],
  }),
};

const React = await import("react");
const { act } = React;
const { createRoot } = await import("react-dom/client");
const { useVoiceRecorder } = await import("../src/features/diary/hooks/useVoiceRecorder");

interface Api {
  start: (entryId: number) => Promise<void>;
}

function Harness({
  api,
  onFinalTranscript,
  onRecordingComplete,
}: {
  api: { current: Api | null };
  onFinalTranscript: (text: string, entryId: unknown) => void;
  onRecordingComplete: (file: File, entryId: unknown) => void;
}) {
  const result = useVoiceRecorder({
    onFinalTranscript: onFinalTranscript as any,
    onRecordingComplete: onRecordingComplete as any,
    onError: () => {},
  });
  api.current = result;
  return null;
}

describe("useVoiceRecorder entry-id capture (PR #139, Finding 2)", () => {
  test("onRecordingComplete reports the entry selected when recording STARTED, not whatever is passed later", async () => {
    FakeMediaRecorder.instances = [];
    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const api: { current: Api | null } = { current: null };
    const completions: Array<[string, unknown]> = [];

    try {
      await act(async () => {
        root.render(
          <Harness
            api={api}
            onFinalTranscript={() => {}}
            onRecordingComplete={(file, entryId) => completions.push([file.name, entryId])}
          />,
        );
      });

      // Recording is started against entry 1.
      await act(async () => {
        await api.current?.start(1);
      });

      const recorder = FakeMediaRecorder.instances[0];
      expect(recorder).toBeDefined();

      // MediaRecorder.onstop is asynchronous in real browsers; simulate
      // that gap by firing it well after start() returned, standing in
      // for "the user switched to a different entry while this was
      // recording, or while stopIfActive() was tearing it down."
      await act(async () => {
        recorder.stop();
      });

      expect(completions.length).toBe(1);
      // BEFORE the fix: onRecordingComplete was called with only (file) —
      // no second argument at all, since the entry id was read from an
      // external "currently selected" ref that the caller consulted at
      // completion time, not threaded through the recording session
      // itself. AFTER the fix: it reports the id captured when start()
      // was called, unconditionally.
      expect(completions[0][1]).toBe(1);
    } finally {
      root.unmount();
    }
  });

  test("onFinalTranscript reports the entry selected when recording STARTED", async () => {
    FakeMediaRecorder.instances = [];
    FakeSpeechRecognition.instances = [];
    const container = dom.window.document.getElementById("root") as HTMLElement;
    const root = createRoot(container);
    const api: { current: Api | null } = { current: null };
    const transcripts: Array<[string, unknown]> = [];

    try {
      await act(async () => {
        root.render(
          <Harness
            api={api}
            onFinalTranscript={(text, entryId) => transcripts.push([text, entryId])}
            onRecordingComplete={() => {}}
          />,
        );
      });

      await act(async () => {
        await api.current?.start(42);
      });

      const recognition = FakeSpeechRecognition.instances[0];
      expect(recognition).toBeDefined();

      await act(async () => {
        recognition.onresult?.({
          resultIndex: 0,
          results: { length: 1, 0: fakeResult("hello world", true) },
        });
      });

      expect(transcripts.length).toBe(1);
      expect(transcripts[0][1]).toBe(42);
    } finally {
      root.unmount();
    }
  });
});
