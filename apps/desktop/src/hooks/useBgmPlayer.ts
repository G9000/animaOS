import { useCallback, useEffect, useRef, useState } from "react";
import { saveAudioFile, loadAudioFile, deleteAudioFile } from "../lib/audioDb";
import {
  DEVICE_BGM_TRACKS_KEY,
  getPortablePreference,
  PORTABLE_PREFERENCES_CHANGED_EVENT,
  setPortablePreference,
} from "../lib/portablePreferences";

export interface BgmTrack {
  id: string;
  name: string;
  trackNum?: string; // e.g. "1.03" — built-ins only
  src: string;
  builtIn: boolean;
}

interface SavedUserTrack { id: string; name: string; }

interface PersistedState {
  currentId: string;
  muted: boolean;
  userTracks: SavedUserTrack[];
}

export const BUILT_IN_TRACKS: BgmTrack[] = [
  { id: "builtin-v",           name: "V",              trackNum: "1.01", src: "/bgm-v.mp3",  builtIn: true },
  { id: "builtin-rebel-path",  name: "THE REBEL PATH", trackNum: "1.03", src: "/bgm.mp3",    builtIn: true },
];

const DEFAULT_STATE: PersistedState = {
  currentId: BUILT_IN_TRACKS[1].id, // default: Rebel Path
  muted: false,
  userTracks: [],
};

function loadState(): PersistedState {
  const portable = getPortablePreference<Partial<PersistedState>>("bgm", {});
  let userTracks: SavedUserTrack[] = [];
  let deviceCurrentId: string | undefined;
  try {
    const raw = localStorage.getItem(DEVICE_BGM_TRACKS_KEY);
    if (raw) {
      const device = JSON.parse(raw) as
        | SavedUserTrack[]
        | { currentId?: unknown; userTracks?: unknown };
      if (Array.isArray(device)) userTracks = device;
      else {
        if (Array.isArray(device.userTracks)) {
          userTracks = device.userTracks as SavedUserTrack[];
        }
        if (typeof device.currentId === "string") deviceCurrentId = device.currentId;
      }
    }
  } catch {}
  return {
    ...DEFAULT_STATE,
    ...portable,
    ...(deviceCurrentId ? { currentId: deviceCurrentId } : {}),
    userTracks,
  };
}

function saveState(s: PersistedState) {
  const currentId = s.currentId.startsWith("builtin-") ? s.currentId : undefined;
  const previous = getPortablePreference<Partial<PersistedState>>("bgm", {});
  setPortablePreference("bgm", {
    ...(currentId
      ? { currentId }
      : previous.currentId
        ? { currentId: previous.currentId }
        : {}),
    muted: s.muted,
  });
  try {
    if (s.userTracks.length > 0) {
      localStorage.setItem(
        DEVICE_BGM_TRACKS_KEY,
        JSON.stringify({
          currentId: currentId ? null : s.currentId,
          userTracks: s.userTracks,
        }),
      );
    } else {
      localStorage.removeItem(DEVICE_BGM_TRACKS_KEY);
    }
  } catch {}
}

export function useBgmPlayer(volume = 0.35) {
  const audioRef     = useRef<HTMLAudioElement | null>(null);
  const blobUrlsRef  = useRef<Map<string, string>>(new Map());
  const tracksRef    = useRef<BgmTrack[]>(BUILT_IN_TRACKS);

  const [tracks,      setTracksState] = useState<BgmTrack[]>(BUILT_IN_TRACKS);
  const [currentId,   setCurrentId]   = useState<string>(DEFAULT_STATE.currentId);
  const [muted,       setMuted]       = useState<boolean>(DEFAULT_STATE.muted);
  const [ready,       setReady]       = useState(false);

  function setTracks(next: BgmTrack[]) {
    tracksRef.current = next;
    setTracksState(next);
  }

  // ── One-time init: restore user tracks from IndexedDB ─────────────────────
  useEffect(() => {
    const state = loadState();
    setMuted(state.muted);

    async function init() {
      const userTracks: BgmTrack[] = [];
      for (const saved of state.userTracks) {
        try {
          const buf = await loadAudioFile(saved.id);
          if (buf) {
            const url = URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" }));
            blobUrlsRef.current.set(saved.id, url);
            userTracks.push({ id: saved.id, name: saved.name, src: url, builtIn: false });
          }
        } catch { /* skip unrestorable tracks */ }
      }

      const all = [...BUILT_IN_TRACKS, ...userTracks];
      setTracks(all);

      const validId = all.find(t => t.id === state.currentId) ? state.currentId : BUILT_IN_TRACKS[1].id;
      const track   = all.find(t => t.id === validId)!;
      setCurrentId(validId);

      const audio = new Audio(track.src);
      audio.loop   = true;
      audio.volume = volume;
      audio.muted  = state.muted;
      audioRef.current = audio;
      audio.play().catch(() => {});

      setReady(true);
    }

    init();

    return () => {
      audioRef.current?.pause();
      if (audioRef.current) audioRef.current.src = "";
      audioRef.current = null;
      blobUrlsRef.current.forEach(url => URL.revokeObjectURL(url));
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const refreshPortableState = () => {
      const state = loadState();
      setMuted(state.muted);
      if (state.currentId.startsWith("builtin-") && state.currentId !== currentId) {
        const track = tracksRef.current.find((item) => item.id === state.currentId);
        if (track && audioRef.current) {
          audioRef.current.src = track.src;
          audioRef.current.load();
          audioRef.current.play().catch(() => {});
          setCurrentId(state.currentId);
        }
      }
    };
    globalThis.addEventListener(
      PORTABLE_PREFERENCES_CHANGED_EVENT,
      refreshPortableState,
    );
    return () =>
      globalThis.removeEventListener(
        PORTABLE_PREFERENCES_CHANGED_EVENT,
        refreshPortableState,
      );
  }, [currentId]);

  // ── Sync muted ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (audioRef.current) audioRef.current.muted = muted;
  }, [muted]);

  // ── Controls ──────────────────────────────────────────────────────────────

  const toggleMute = useCallback(() => {
    setMuted(m => {
      const next = !m;
      saveState({ ...loadState(), muted: next });
      return next;
    });
  }, []);

  const selectTrack = useCallback((id: string) => {
    const track = tracksRef.current.find(t => t.id === id);
    if (!track || !audioRef.current) return;
    audioRef.current.src = track.src;
    audioRef.current.load();
    audioRef.current.play().catch(() => {});
    setCurrentId(id);
    saveState({ ...loadState(), currentId: id });
  }, []);

  const addTrack = useCallback(async (file: File) => {
    const id  = `user-${Date.now()}`;
    const buf = await file.arrayBuffer();
    await saveAudioFile(id, buf);
    const url = URL.createObjectURL(new Blob([buf], { type: file.type || "audio/mpeg" }));
    blobUrlsRef.current.set(id, url);
    const name  = file.name.replace(/\.[^.]+$/, "");
    const track: BgmTrack = { id, name, src: url, builtIn: false };
    const next  = [...tracksRef.current, track];
    setTracks(next);
    saveState({ ...loadState(), userTracks: next.filter(t => !t.builtIn).map(t => ({ id: t.id, name: t.name })) });
  }, []);

  const removeTrack = useCallback((id: string) => {
    const track = tracksRef.current.find(t => t.id === id);
    if (!track || track.builtIn) return;

    const blobUrl = blobUrlsRef.current.get(id);
    if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrlsRef.current.delete(id); }
    deleteAudioFile(id).catch(() => {});

    const next = tracksRef.current.filter(t => t.id !== id);
    setTracks(next);
    saveState({ ...loadState(), userTracks: next.filter(t => !t.builtIn).map(t => ({ id: t.id, name: t.name })) });

    setCurrentId(cur => {
      if (cur !== id) return cur;
      const fallback = BUILT_IN_TRACKS[1];
      if (audioRef.current) {
        audioRef.current.src = fallback.src;
        audioRef.current.load();
        audioRef.current.play().catch(() => {});
      }
      saveState({ ...loadState(), currentId: fallback.id });
      return fallback.id;
    });
  }, []);

  const resetBgm = useCallback(() => {
    tracksRef.current.filter(t => !t.builtIn).forEach(t => {
      const url = blobUrlsRef.current.get(t.id);
      if (url) { URL.revokeObjectURL(url); blobUrlsRef.current.delete(t.id); }
      deleteAudioFile(t.id).catch(() => {});
    });
    setTracks(BUILT_IN_TRACKS);
    const fallback = BUILT_IN_TRACKS[1];
    setCurrentId(fallback.id);
    setMuted(false);
    if (audioRef.current) {
      audioRef.current.muted = false;
      audioRef.current.src   = fallback.src;
      audioRef.current.load();
      audioRef.current.play().catch(() => {});
    }
    saveState(DEFAULT_STATE);
  }, []);

  return {
    tracks,
    currentId,
    currentTrack: tracks.find(t => t.id === currentId),
    muted,
    ready,
    toggleMute,
    selectTrack,
    addTrack,
    removeTrack,
    resetBgm,
  };
}
