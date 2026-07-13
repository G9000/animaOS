import { AsciiBackground } from "@anima/ascii-motion";

export default function AnimaLogoBg() {
  return (
    <AsciiBackground
      src="/login-bg.mp4"
      cols={140}
      glyphSet="K·▪"
      contrast={1.15}
      brightness={-8}
      color={true}
      className="fixed inset-0"
    />
  );
}
