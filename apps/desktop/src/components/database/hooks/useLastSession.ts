import { useCallback } from "react";
import { useLocalStorage } from "./useLocalStorage";

interface LastSession {
  lastTable?: string;
  lastView: "tables" | "rows" | "query" | "schema" | "dashboard" | "relations";
  lastQuery?: string;
  timestamp: number;
}

export function useLastSession() {
  const [session, setSession] = useLocalStorage<LastSession | null>("db-last-session", null);

  const saveSession = useCallback((updates: Partial<LastSession>) => {
    setSession((prev) => ({
      ...(prev || { lastView: "dashboard", timestamp: Date.now() }),
      ...updates,
      timestamp: Date.now(),
    }));
  }, [setSession]);

  const clearSession = () => {
    setSession(null);
  };

  const restoreSession = () => {
    if (!session) return null;
    
    // Only restore if session is less than 24 hours old
    const age = Date.now() - session.timestamp;
    if (age > 24 * 60 * 60 * 1000) {
      return null;
    }
    
    return session;
  };

  return {
    session,
    saveSession,
    clearSession,
    restoreSession,
  };
}
