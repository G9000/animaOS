import { useState, useEffect, useCallback } from "react";
import { cn } from "../utils/cn";

export type ToastType = "success" | "error" | "warning" | "info";

export interface ToastData {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export interface ToastContainerProps {
  /** Maximum number of toasts to display at once. Older toasts are removed. */
  maxToasts?: number;
  /** Position of the toast container */
  position?: "bottom-right" | "bottom-left" | "top-right" | "top-left";
}

const toastStyles: Record<ToastType, string> = {
  success: "bg-success/10 border-success/30 text-success",
  error: "bg-destructive/10 border-destructive/30 text-destructive",
  warning: "bg-warning/10 border-warning/30 text-warning",
  info: "bg-primary/10 border-primary/30 text-primary",
};

const positionStyles: Record<NonNullable<ToastContainerProps["position"]>, string> = {
  "bottom-right": "bottom-4 right-4",
  "bottom-left": "bottom-4 left-4",
  "top-right": "top-4 right-4",
  "top-left": "top-4 left-4",
};

function ToastItem({
  id,
  message,
  type,
  duration = 5000,
  action,
  onDismiss,
}: ToastData & { onDismiss: (id: string) => void }) {
  const [progress, setProgress] = useState(100);
  const [isPaused, setIsPaused] = useState(false);

  useEffect(() => {
    if (isPaused) return;

    const startTime = Date.now();
    const endTime = startTime + duration;

    const updateProgress = () => {
      const now = Date.now();
      const remaining = Math.max(0, endTime - now);
      const newProgress = (remaining / duration) * 100;
      setProgress(newProgress);

      if (remaining > 0) {
        requestAnimationFrame(updateProgress);
      } else {
        onDismiss(id);
      }
    };

    const animationFrame = requestAnimationFrame(updateProgress);
    return () => cancelAnimationFrame(animationFrame);
  }, [id, duration, isPaused, onDismiss]);

  return (
    <div
      className={cn(
        "relative flex items-start gap-3 px-4 py-3 border min-w-[300px] max-w-[400px] animate-slide-in",
        toastStyles[type],
      )}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
      role="alert"
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm">{message}</p>
        {action && (
          <button
            onClick={() => {
              action.onClick();
              onDismiss(id);
            }}
            className="mt-2 text-xs font-medium underline hover:no-underline opacity-80 hover:opacity-100"
          >
            {action.label}
          </button>
        )}
      </div>
      <button
        onClick={() => onDismiss(id)}
        className="shrink-0 p-1 -mr-1 -mt-1 opacity-50 hover:opacity-100 transition-opacity"
        aria-label="Dismiss"
      >
        <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>

      <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-black/10 overflow-hidden">
        <div
          className="h-full bg-current opacity-30 transition-none"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

let toastListeners: Array<(toast: ToastData) => void> = [];

export function showToast(toast: Omit<ToastData, "id">) {
  const id = Math.random().toString(36).substring(2, 9);
  toastListeners.forEach((listener) => listener({ ...toast, id }));
}

export function showSuccess(message: string, duration?: number) {
  showToast({ message, type: "success", duration });
}

export function showError(message: string, duration?: number) {
  showToast({ message, type: "error", duration });
}

export function showWarning(message: string, duration?: number) {
  showToast({ message, type: "warning", duration });
}

export function showInfo(message: string, duration?: number) {
  showToast({ message, type: "info", duration });
}

export function ToastContainer({ maxToasts = 5, position = "bottom-right" }: ToastContainerProps) {
  const [toasts, setToasts] = useState<ToastData[]>([]);

  useEffect(() => {
    const handleToast = (toast: ToastData) => {
      setToasts((prev) => {
        // Add new toast and limit to maxToasts (remove oldest)
        const newToasts = [...prev, toast];
        if (newToasts.length > maxToasts) {
          return newToasts.slice(newToasts.length - maxToasts);
        }
        return newToasts;
      });
    };

    toastListeners.push(handleToast);
    return () => {
      toastListeners = toastListeners.filter((l) => l !== handleToast);
    };
  }, [maxToasts]);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className={cn("fixed z-[100] flex flex-col gap-2 pointer-events-none", positionStyles[position])}>
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <ToastItem {...toast} onDismiss={dismissToast} />
        </div>
      ))}
    </div>
  );
}
