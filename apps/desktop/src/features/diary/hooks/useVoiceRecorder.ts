import { useEffect, useRef, useState } from "react";
import {
  buildRecordingFilename,
  chooseRecordingMimeType,
  getSpeechRecognitionConstructor,
  type SpeechRecognitionLike,
} from "../lib/speech";

// The id of whichever entry a recording session belongs to. Always a real
// DiaryEntryData.id (never null) — the caller only starts a recording
// against a concrete, currently-selected entry (see DiaryWorkspace's
// onToggleRecording, guarded on `selectedEntry`).
export type VoiceRecordingEntryId = number;

export interface UseVoiceRecorderOptions {
  // Called with each finalized speech-to-text chunk (already trimmed, with
  // a trailing space) as it arrives, plus the id of the entry that was
  // SELECTED WHEN THIS RECORDING SESSION STARTED (not whatever happens to
  // be selected when the chunk actually arrives — recognition results are
  // asynchronous, so those can differ if the user switches entries mid-
  // recording). The caller owns deciding what to do if that id no longer
  // matches the currently open entry (see the doc comment at the call
  // site in DiaryWorkspace.tsx) — this hook only reports identity, it
  // never assumes it is still safe to insert.
  onFinalTranscript: (text: string, entryId: VoiceRecordingEntryId) => void;
  // Called once with the completed recording, only if any audio was
  // actually captured, plus the id of the entry that was selected when
  // recording STARTED (see onFinalTranscript above — MediaRecorder's
  // onstop is likewise asynchronous, so "whatever is selected now" at
  // completion time is not necessarily the entry this audio was recorded
  // against).
  onRecordingComplete: (file: File, entryId: VoiceRecordingEntryId) => void;
  onError: (message: string | null) => void;
}

export interface UseVoiceRecorderResult {
  recording: boolean;
  speechAvailable: boolean;
  liveTranscript: string;
  // Finding 2 (PR #139): takes the id of the entry this session is being
  // recorded against, captured here (in a ref, before anything async
  // happens) rather than left for onstop/onresult to re-derive later from
  // whatever is selected by then.
  start: (entryId: VoiceRecordingEntryId) => Promise<void>;
  stop: () => void;
  // "Abandon" variant for switching entries mid-recording (still finalizes
  // whatever was captured via the same onstop/onRecordingComplete path —
  // it does not discard audio, it just skips the graceful
  // recognition.stop() in favor of an immediate abort()). Idempotent: a
  // no-op if nothing is recording.
  stopIfActive: () => void;
}

/**
 * Voice-note recording + live speech-to-text, extracted verbatim (Task 12)
 * from DiaryWorkspace.tsx. Every ref, guard, and event handler here is
 * unchanged from the pre-extraction code — only the two points that used
 * to reach directly into DiaryWorkspace's state (attaching the finished
 * recording to the currently-selected entry, inserting a final transcript
 * into the live editor) now go through the callbacks above, which fire at
 * the exact same moments (recorder.onstop, recognition.onresult) as before.
 */
export function useVoiceRecorder(options: UseVoiceRecorderOptions): UseVoiceRecorderResult {
  // Always call the LATEST callbacks, since a recording session can easily
  // outlive the render that started it.
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [recording, setRecording] = useState(false);
  const [speechAvailable, setSpeechAvailable] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const recordedChunksRef = useRef<BlobPart[]>([]);
  // Finding 2 (PR #139): set once, synchronously, at the top of start() —
  // before the getUserMedia await — and read by both onstop and
  // recognition.onresult instead of either of them reaching into
  // whatever entry is selected by the time they actually fire.
  const recordingEntryIdRef = useRef<VoiceRecordingEntryId | null>(null);

  useEffect(() => {
    setSpeechAvailable(getSpeechRecognitionConstructor() !== null);
    return () => {
      recognitionRef.current?.abort();
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  const startSpeechRecognition = () => {
    const SpeechRecognition = getSpeechRecognitionConstructor();
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = navigator.language || "en-US";
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript ?? "";
        if (result.isFinal) {
          finalText += transcript;
        } else {
          interimText += transcript;
        }
      }
      if (finalText.trim()) {
        const entryId = recordingEntryIdRef.current;
        if (entryId != null) {
          optionsRef.current.onFinalTranscript(`${finalText.trim()} `, entryId);
        }
      }
      setLiveTranscript(interimText.trim());
    };
    recognition.onerror = () => {
      setLiveTranscript("");
    };
    recognition.onend = () => {
      setLiveTranscript("");
    };

    try {
      recognition.start();
      recognitionRef.current = recognition;
    } catch {
      recognitionRef.current = null;
    }
  };

  const releaseRecordingResources = () => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
    mediaRecorderRef.current = null;
    recognitionRef.current = null;
    recordingEntryIdRef.current = null;
  };

  const start = async (entryId: VoiceRecordingEntryId) => {
    if (recording) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      optionsRef.current.onError("Audio recording is not available in this environment.");
      return;
    }

    // Captured synchronously, before the getUserMedia await below ever
    // yields to the event loop — this is "the entry selected when
    // recording started", fixed for the whole session regardless of what
    // the user selects afterward.
    recordingEntryIdRef.current = entryId;
    optionsRef.current.onError(null);
    recordedChunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const requestedMimeType = chooseRecordingMimeType();
      const recorder = requestedMimeType
        ? new MediaRecorder(stream, { mimeType: requestedMimeType })
        : new MediaRecorder(stream);

      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          recordedChunksRef.current.push(event.data);
        }
      };
      recorder.onerror = () => {
        optionsRef.current.onError("Recording failed.");
        setRecording(false);
        releaseRecordingResources();
      };
      recorder.onstop = () => {
        const chunks = recordedChunksRef.current;
        const mimeType = recorder.mimeType || requestedMimeType || "audio/webm";
        const capturedEntryId = recordingEntryIdRef.current;
        if (chunks.length > 0 && capturedEntryId != null) {
          const blob = new Blob(chunks, { type: mimeType });
          const file = new File([blob], buildRecordingFilename(new Date(), mimeType), {
            type: mimeType,
          });
          optionsRef.current.onRecordingComplete(file, capturedEntryId);
        }
        recordedChunksRef.current = [];
        setRecording(false);
        setLiveTranscript("");
        releaseRecordingResources();
      };

      startSpeechRecognition();
      recorder.start(1000);
      setRecording(true);
    } catch (err) {
      optionsRef.current.onError(err instanceof Error ? err.message : "Could not start recording.");
      setRecording(false);
      releaseRecordingResources();
    }
  };

  const stop = () => {
    recognitionRef.current?.stop();
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
      return;
    }
    setRecording(false);
    releaseRecordingResources();
  };

  const stopIfActive = () => {
    if (mediaRecorderRef.current?.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    recognitionRef.current?.abort();
    setRecording(false);
    setLiveTranscript("");
  };

  return { recording, speechAvailable, liveTranscript, start, stop, stopIfActive };
}
