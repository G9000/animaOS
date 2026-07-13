import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

interface LayoutActionsContextValue {
  dashboardReset: (() => void) | null;
  nudgeCount: number;
  registerDashboardReset: (fn: (() => void) | null) => void;
  registerNudgeCount: (count: number) => void;
}

const LayoutActionsContext = createContext<LayoutActionsContextValue>({
  dashboardReset: null,
  nudgeCount: 0,
  registerDashboardReset: () => {},
  registerNudgeCount: () => {},
});

export function LayoutActionsProvider({ children }: { children: ReactNode }) {
  const [dashboardReset, setDashboardReset] = useState<(() => void) | null>(null);
  const [nudgeCount, setNudgeCount] = useState(0);

  const registerDashboardReset = useCallback(
    (fn: (() => void) | null) => setDashboardReset(() => fn),
    [],
  );
  const registerNudgeCount = useCallback((count: number) => setNudgeCount(count), []);

  return (
    <LayoutActionsContext.Provider value={{ dashboardReset, nudgeCount, registerDashboardReset, registerNudgeCount }}>
      {children}
    </LayoutActionsContext.Provider>
  );
}

export function useLayoutActions() {
  return useContext(LayoutActionsContext);
}
