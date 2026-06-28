import { useEffect, useRef, useState } from "react";

export function useBgm(src: string, volume = 0.35) {
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const [muted, setMuted] = useState(() => {
    try { return localStorage.getItem("anima_bgm_muted") === "true"; } catch { return false; }
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
    try { localStorage.setItem("anima_bgm_muted", String(muted)); } catch {}
  }, [muted]);

  const toggleMute = () => setMuted(m => !m);

  return { muted, toggleMute };
}
