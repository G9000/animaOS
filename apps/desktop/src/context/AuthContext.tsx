import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react";
import type { User } from "@anima/api-client";
import {
  api,
  clearUnlockToken,
  CORE_RUNTIME_RESTART_REQUIRED_EVENT,
  getUnlockToken,
  UNLOCK_SESSION_LOCKED_EVENT,
} from "../lib/api";
import { purgeGreetingStorage } from "../lib/greetingCache";
import { getDaemonStatus, refreshDaemonRuntimeNonce, startDaemon } from "../lib/daemon";
import {
  clearPortablePreferences,
  hydratePortablePreferences,
} from "../lib/portablePreferences";

interface AuthContextType {
  user: User | null;
  setUser: (user: User | null) => void;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  isLoading: boolean;
  isProvisioned: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

const HEALTH_BOOT_RETRIES = 20;
const HEALTH_BOOT_RETRY_MS = 500;
const DAEMON_STARTUP_RETRIES = 90;

function purgeLegacyStoredUser(): void {
  try {
    localStorage.removeItem("anima_user");
    localStorage.removeItem("anima_last_user");
  } catch {
    // Ignore storage failures.
  }
}

function isNetworkError(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  if (!(error instanceof Error)) return false;
  return /failed to fetch|networkerror/i.test(error.message);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForRuntimeStartup(): Promise<void> {
  for (let attempt = 0; attempt < DAEMON_STARTUP_RETRIES; attempt += 1) {
    try {
      const status = await getDaemonStatus();
      if (status.state !== "starting") {
        return;
      }
    } catch {
      // If daemon status is unavailable, fall back to direct runtime health probing.
      return;
    }
    await delay(HEALTH_BOOT_RETRY_MS);
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoading, setIsLoading] = useState(true);
  const [isProvisioned, setIsProvisioned] = useState(false);
  const [user, setUser] = useState<User | null>(() => {
    purgeLegacyStoredUser();
    return null;
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      let healthAvailable = false;
      try {
        await startDaemon().catch(async () => {
          await refreshDaemonRuntimeNonce().catch(() => {});
        });
        await waitForRuntimeStartup();
        for (let attempt = 0; attempt < HEALTH_BOOT_RETRIES; attempt += 1) {
          try {
            const health = await api.system.health();
            healthAvailable = true;
            if (!cancelled) setIsProvisioned(health.provisioned === true);
            break;
          } catch (error) {
            if (!isNetworkError(error) || attempt === HEALTH_BOOT_RETRIES - 1) {
              break;
            }
            await delay(HEALTH_BOOT_RETRY_MS);
          }
        }

        if (!healthAvailable && !cancelled) setIsProvisioned(false);

        const token = getUnlockToken();
        if (!token) {
          if (!cancelled) setUser(null);
          return;
        }
        const me = await api.auth.me();
        await hydratePortablePreferences(me.id);
        if (!cancelled) setUser(me);
      } catch (error) {
        if (!isNetworkError(error)) {
          clearUnlockToken();
        }
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const handleRuntimeRestartRequired = () => {
      window.alert(
        "CoreFS cutover is committed. Restart animaOS now to switch to fresh Runtime and retire the plaintext legacy Runtime source. Further portable writes remain blocked until restart.",
      );
    };

    globalThis.addEventListener(
      CORE_RUNTIME_RESTART_REQUIRED_EVENT,
      handleRuntimeRestartRequired,
    );
    return () => {
      globalThis.removeEventListener(
        CORE_RUNTIME_RESTART_REQUIRED_EVENT,
        handleRuntimeRestartRequired,
      );
    };
  }, []);

  useEffect(() => {
    const handleLockedSession = () => {
      // Decrypted greeting/dream handoffs must not outlive the unlock
      // session (PR #130 review).
      purgeGreetingStorage();
      clearPortablePreferences();
      setUser(null);
    };

    globalThis.addEventListener(UNLOCK_SESSION_LOCKED_EVENT, handleLockedSession);
    return () => {
      globalThis.removeEventListener(UNLOCK_SESSION_LOCKED_EVENT, handleLockedSession);
    };
  }, []);

  const logout = async () => {
    try {
      await api.auth.logout();
    } catch {
      // ignore
    }
    clearUnlockToken();
    purgeGreetingStorage();
    clearPortablePreferences();
    setUser(null);
  };

  const handleSetUser = (u: User | null) => {
    const shouldHydrate = u !== null && user?.id !== u.id;
    setUser(u);
    if (u) {
      setIsProvisioned(true);
      if (shouldHydrate) {
        void hydratePortablePreferences(u.id).catch(() => {
          // Exact legacy keys remain for retry when migration cannot be verified.
        });
      }
    } else {
      clearPortablePreferences();
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        setUser: handleSetUser,
        logout,
        isAuthenticated: !!user,
        isLoading,
        isProvisioned,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
