import {
  createContext,
  useContext,
  useState,
  useEffect,
  type ReactNode,
} from "react";
import {
  api,
  clearUnlockToken,
  getUnlockToken,
  type User,
} from "../lib/api";

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

function purgeLegacyStoredUser(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore storage failures.
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
      try {
        // Check provisioning state from health endpoint.
        try {
          const health = await api.system.health();
          if (!cancelled) setIsProvisioned(health.provisioned === true);
        } catch {
          // Server unreachable — assume not provisioned.
        }

        const token = getUnlockToken();
        if (!token) {
          if (!cancelled) setUser(null);
          return;
        }
        const me = await api.auth.me();
        if (!cancelled) setUser(me);
      } catch {
        clearUnlockToken();
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
