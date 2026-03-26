interface Props {
  id: string;
  caption?: string;
}

function extractVideoId(input: string): string {
  // Already a bare ID (no slashes or dots)
  if (!input.includes("/") && !input.includes(".")) return input;

  try {
    const url = new URL(input);
    // youtu.be/ID or youtube.com/shorts/ID
    const pathParts = url.pathname.split("/").filter(Boolean);
    if (url.hostname === "youtu.be") return pathParts[0];
    if (pathParts.includes("shorts")) return pathParts[pathParts.indexOf("shorts") + 1];
    // youtube.com/watch?v=ID
    return url.searchParams.get("v") ?? input;
  } catch {
    return input;
  }
}

export default function YouTubeEmbed({ id, caption }: Props) {
  const videoId = extractVideoId(id);

  return (
    <figure className="my-12 -mx-6">
      <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
        <iframe
          src={`https://www.youtube-nocookie.com/embed/${videoId}`}
          title={caption ?? "Video"}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          className="absolute inset-0 w-full h-full border-0"
        />
      </div>
      {caption && (
        <figcaption className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/30 mt-3 px-6">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
