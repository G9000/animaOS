import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { CoreFSSecurityStatus } from "@anima/api-client";
import { useAuth } from "./AuthContext";
import { api } from "../lib/api";

interface CoreFSReadinessContextValue {
  status: CoreFSSecurityStatus | null;
  loading: boolean;
  error: string | null;
  catalogReady: boolean;
  refresh: () => Promise<void>;
}

const CoreFSReadinessContext = createContext<CoreFSReadinessContextValue | null>(
  null,
);

export function catalogNavigationAvailable(
  status: CoreFSSecurityStatus | null,
): boolean {
  if (!status) return false;
  return (
    !status.filesystemAvailable ||
    status.readiness.capabilities.includes("navigation")
  );
}

export function CoreFSReadinessProvider({
  children,
  initialStatus = null,
}: {
  children: ReactNode;
  initialStatus?: CoreFSSecurityStatus | null;
}) {
  const { isAuthenticated } = useAuth();
  const [status, setStatus] = useState<CoreFSSecurityStatus | null>(initialStatus);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!isAuthenticated) {
      setStatus(null);
      setError(null);
      return;
    }
    setLoading(true);
    try {
      setStatus(await api.corefs.securityStatus());
      setError(null);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "CoreFS readiness is unavailable.",
      );
    } finally {
      setLoading(false);
    }
  }, [isAuthenticated]);

  useEffect(() => {
    void refresh();
    if (!isAuthenticated) return undefined;
    const timer = window.setInterval(() => void refresh(), 2_000);
    return () => window.clearInterval(timer);
  }, [isAuthenticated, refresh]);

  const value = useMemo(
    () => ({
      status,
      loading,
      error,
      catalogReady: catalogNavigationAvailable(status),
      refresh,
    }),
    [status, loading, error, refresh],
  );

  return (
    <CoreFSReadinessContext.Provider value={value}>
      {children}
    </CoreFSReadinessContext.Provider>
  );
}

export function useCoreFSReadiness(): CoreFSReadinessContextValue {
  const value = useContext(CoreFSReadinessContext);
  if (!value) {
    throw new Error(
      "useCoreFSReadiness must be used within CoreFSReadinessProvider",
    );
  }
  return value;
}
