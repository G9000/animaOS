import { useEffect, useRef, useState } from "react";
import { getRuntimeRequestHeaders } from "../lib/api";
import { API_ORIGIN } from "../lib/runtime";

interface AuthImageProps {
  src: string;
  alt: string;
  className?: string;
}

/**
 * Renders a server-relative image URL that requires the x-anima-unlock token.
 * Fetches the image as a blob and renders it from an object URL.
 */
export function AuthImage({ src, alt, className }: AuthImageProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const blobRef = useRef<string | null>(null);

  useEffect(() => {
    if (!src) return;

    let cancelled = false;
    const fullUrl = src.startsWith("http") ? src : `${API_ORIGIN}${src}`;
    fetch(fullUrl, { headers: getRuntimeRequestHeaders() })
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (cancelled || !blob) return;
        const url = URL.createObjectURL(blob);
        blobRef.current = url;
        setBlobUrl(url);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
      if (blobRef.current) {
        URL.revokeObjectURL(blobRef.current);
        blobRef.current = null;
      }
    };
  }, [src]);

  if (!blobUrl) {
    return <div className={className} style={{ background: "var(--muted)" }} />;
  }
  return <img src={blobUrl} alt={alt} className={className} />;
}
