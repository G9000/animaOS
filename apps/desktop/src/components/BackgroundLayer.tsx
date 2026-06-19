import { useBackground } from "../hooks/useBackground";

export default function BackgroundLayer() {
  const { config, url } = useBackground();

  const dimStyle = { opacity: config.dim ?? 0 };
  const blurStyle = { filter: `blur(${config.blur ?? 0}px)` };

  if (config.type === "default") {
    return (
      <div
        className="fixed inset-0 -z-10 bg-background transition-colors"
        aria-hidden="true"
      />
    );
  }

  if (config.type === "color") {
    return (
      <div
        className="fixed inset-0 -z-10 transition-colors"
        style={{ backgroundColor: config.value }}
        aria-hidden="true"
      >
        <div
          className="absolute inset-0 bg-black transition-opacity"
          style={dimStyle}
        />
      </div>
    );
  }

  if (config.type === "gradient") {
    return (
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: config.value,
          backgroundSize: "cover",
        }}
        aria-hidden="true"
      >
        <div
          className="absolute inset-0 bg-black transition-opacity"
          style={dimStyle}
        />
      </div>
    );
  }

  if (config.type === "image") {
    if (config.fit === "repeat" && url) {
      return (
        <div
          className="fixed inset-0 -z-10"
          style={{
            backgroundImage: `url(${url})`,
            backgroundRepeat: "repeat",
            backgroundSize: "auto",
            ...blurStyle,
          }}
          aria-hidden="true"
        >
          <div
            className="absolute inset-0 bg-black transition-opacity"
            style={dimStyle}
          />
        </div>
      );
    }

    return (
      <div className="fixed inset-0 -z-10" aria-hidden="true">
        {url ? (
          <img
            src={url}
            alt=""
            className="w-full h-full transition-all"
            style={{
              ...blurStyle,
              objectFit: config.fit === "contain" ? "contain" : "cover",
            }}
          />
        ) : null}
        <div
          className="absolute inset-0 bg-black transition-opacity"
          style={dimStyle}
        />
      </div>
    );
  }

  if (config.type === "video") {
    return (
      <div className="fixed inset-0 -z-10" aria-hidden="true">
        {url ? (
          <video
            src={url}
            autoPlay
            muted
            loop
            playsInline
            className="w-full h-full transition-all"
            style={{
              ...blurStyle,
              objectFit: config.fit === "contain" ? "contain" : "cover",
            }}
          />
        ) : null}
        <div
          className="absolute inset-0 bg-black transition-opacity"
          style={dimStyle}
        />
      </div>
    );
  }

  return null;
}
