import { Outlet } from "react-router-dom";
import { AsciiBackground } from "@anima/ascii-motion";
import { AsciiSettingsProvider, useAsciiSettings } from "../context/AsciiSettingsContext";
import { AtmosphereControls } from "./atmosphere/AtmosphereControls";

function AtmosphereBackground() {
  const { settings, srcOverride, srcOverrideType } = useAsciiSettings();
  return (
    <AsciiBackground
      src={srcOverride ?? "/login-bg.mp4"}
      mediaType={srcOverrideType}
      cols={settings.cols}
      color={settings.color}
      contrast={settings.contrast}
      brightness={settings.brightness}
      edgeDetect={settings.edgeDetect}
      glyphSet={settings.glyphSet}
      className="fixed inset-0"
    />
  );
}

export function AtmosphereShell() {
  return (
    <AsciiSettingsProvider>
      <AtmosphereBackground />
      <Outlet />
      <AtmosphereControls />
    </AsciiSettingsProvider>
  );
}
