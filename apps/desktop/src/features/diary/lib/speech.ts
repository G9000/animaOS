const RECORDING_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/wav",
];

export interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

export interface SpeechRecognitionResultLike {
  readonly isFinal: boolean;
  readonly length: number;
  [index: number]: SpeechRecognitionAlternativeLike;
}

export interface SpeechRecognitionEventLike {
  readonly resultIndex: number;
  readonly results: {
    readonly length: number;
    [index: number]: SpeechRecognitionResultLike;
  };
}

export interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

export type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

interface MediaRecorderSupport {
  isTypeSupported?: (mimeType: string) => boolean;
}

export function getSpeechRecognitionConstructor(
  scope: unknown = globalThis,
): SpeechRecognitionConstructor | null {
  const candidate = scope as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return candidate.SpeechRecognition ?? candidate.webkitSpeechRecognition ?? null;
}

export function chooseRecordingMimeType(
  recorder: MediaRecorderSupport | undefined = globalThis.MediaRecorder,
): string {
  if (!recorder?.isTypeSupported) return "";
  return RECORDING_MIME_CANDIDATES.find((candidate) => recorder.isTypeSupported?.(candidate)) ?? "";
}

export function extensionForAudioMimeType(mimeType: string): string {
  const normalized = mimeType.split(";", 1)[0].trim().toLowerCase();
  if (normalized === "audio/mp4" || normalized === "audio/m4a") return "m4a";
  if (normalized === "audio/wav" || normalized === "audio/wave") return "wav";
  if (normalized === "audio/ogg") return "ogg";
  return "webm";
}

export function buildRecordingFilename(date: Date, mimeType: string): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  const stamp = [
    date.getUTCFullYear(),
    pad(date.getUTCMonth() + 1),
    pad(date.getUTCDate()),
    pad(date.getUTCHours()),
    pad(date.getUTCMinutes()),
    pad(date.getUTCSeconds()),
  ].join("-");
  return `diary-voice-${stamp}.${extensionForAudioMimeType(mimeType)}`;
}

export function appendTranscript(existing: string, transcript: string): string {
  const base = existing.trim();
  const next = transcript.trim();
  if (!next) return base;
  if (!base) return next;
  return `${base}\n\n${next}`;
}
