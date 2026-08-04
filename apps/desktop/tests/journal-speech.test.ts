import { describe, expect, test } from "bun:test";

import {
  appendTranscript,
  buildRecordingFilename,
  chooseRecordingMimeType,
  extensionForAudioMimeType,
  getSpeechRecognitionConstructor,
} from "../src/features/diary/lib/speech";

describe("journal speech helpers", () => {
  test("chooses the first supported recording mime type", () => {
    const mimeType = chooseRecordingMimeType({
      isTypeSupported: (candidate) => candidate === "audio/webm",
    });

    expect(mimeType).toBe("audio/webm");
  });

  test("falls back to browser default when MediaRecorder support cannot be inspected", () => {
    expect(chooseRecordingMimeType()).toBe("");
  });

  test("maps audio mime types to stable file extensions", () => {
    expect(extensionForAudioMimeType("audio/webm;codecs=opus")).toBe("webm");
    expect(extensionForAudioMimeType("audio/mp4")).toBe("m4a");
    expect(extensionForAudioMimeType("audio/wav")).toBe("wav");
    expect(extensionForAudioMimeType("audio/ogg")).toBe("ogg");
    expect(extensionForAudioMimeType("")).toBe("webm");
  });

  test("builds timestamped recording filenames", () => {
    const date = new Date("2026-06-05T08:09:10Z");

    expect(buildRecordingFilename(date, "audio/webm")).toBe(
      "diary-voice-2026-06-05-08-09-10.webm",
    );
  });

  test("appends transcript text without duplicating whitespace", () => {
    expect(appendTranscript("", "  hello world  ")).toBe("hello world");
    expect(appendTranscript("First paragraph", "second paragraph")).toBe(
      "First paragraph\n\nsecond paragraph",
    );
    expect(appendTranscript("First paragraph\n\n", "  ")).toBe("First paragraph");
  });

  test("detects standard and webkit speech recognition constructors", () => {
    class StandardRecognition {}
    class WebKitRecognition {}

    expect(
      getSpeechRecognitionConstructor({
        SpeechRecognition: StandardRecognition,
        webkitSpeechRecognition: WebKitRecognition,
      }),
    ).toBe(StandardRecognition);
    expect(
      getSpeechRecognitionConstructor({
        webkitSpeechRecognition: WebKitRecognition,
      }),
    ).toBe(WebKitRecognition);
    expect(getSpeechRecognitionConstructor({})).toBeNull();
  });
});
