export type DesktopPackagePlatform = "windows" | "macos" | "debian" | "rpm";

export function resolveDesktopPackageEnvironment(
  platform: DesktopPackagePlatform,
  configuredVersion: string,
  source: NodeJS.ProcessEnv = process.env,
): { environment: NodeJS.ProcessEnv; version: string } {
  const version = source.ANIMA_DESKTOP_VERSION_OVERRIDE ?? configuredVersion;
  return {
    environment: {
      ...source,
      ANIMA_DESKTOP_VERSION_OVERRIDE: version,
      ANIMA_DRAFT_CLEANUP_RELEASE: "1",
      ANIMA_INSTALLER_FAMILY: platform,
    },
    version,
  };
}
