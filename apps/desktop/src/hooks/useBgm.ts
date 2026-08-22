import { useEffect, useRef, useState } from "react";
import { getPortablePreference, setPortablePreference } from "../lib/portablePreferences";

export function useBgm(src: string, volume = 0.35) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const [muted, setMuted] = useState(() => {
    return getPortablePreference<{ muted?: boolean }>("bgm", {}).muted ?? false;
  });

  useEffect(() => {
    const audio = new Audio(src);
    audio.loop = true;
    audio.volume = volume;
    audio.muted = muted;
    audioRef.current = audio;
    audio.play().catch(() => {});
    return () => { audio.pause(); audio.src = ""; audioRef.current = null; };
  }, [src]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.muted = muted;
    setPortablePreference("bgm", {
      ...getPortablePreference<Record<string, unknown>>("bgm", {}),
      muted,
    });
  }, [muted]);

  const toggleMute = () => setMuted(m => !m);

  return { muted, toggleMute };
}
