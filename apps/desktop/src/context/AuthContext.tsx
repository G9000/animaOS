import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react";
import type { User } from "@anima/api-client";
import { api, clearUnlockToken, getUnlockToken } from "../lib/api";

interface AuthContextType {
  user: User | null;
  setUser: (user: User | null) => void;
  logout: () => Promise<void>;
  isAuthenticated: boolean;
  isLoading: boolean;
  isProvisioned: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

const STORAGE_KEY = "anima_user";
const HEALTH_BOOT_RETRIES = 20;
const HEALTH_BOOT_RETRY_MS = 500;

function purgeLegacyStoredUser(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
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

  const logout = async () => {
    try {
      await api.auth.logout();
    } catch {
      // ignore
    }
    clearUnlockToken();
    setUser(null);
  };

  const handleSetUser = (u: User | null) => {
    setUser(u);
    if (u) setIsProvisioned(true);
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
