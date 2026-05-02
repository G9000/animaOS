import React, { useState, useEffect, useCallback } from "react";
import { Icons } from "./Icons";

export type ToastType = "success" | "error" | "warning" | "info";

export interface Toast {
  id: string;
  message: string;
  type: ToastType;
  duration?: number;
  action?: {
    label: string;
    onClick: () => void;
  };
}

interface ToastItemProps extends Toast {
  onDismiss: (id: string) => void;
}

const toastStyles: Record<ToastType, string> = {
  success: "bg-green-500/10 border-green-500/30 text-green-400",
  error: "bg-destructive/10 border-destructive/30 text-destructive",
  warning: "bg-amber-500/10 border-amber-500/30 text-amber-400",
  info: "bg-primary/10 border-primary/30 text-primary",
};

const toastIcons: Record<ToastType, () => React.ReactElement> = {
  success: Icons.Check,
  error: Icons.Warning,
  warning: Icons.Warning,
  info: Icons.Eye,
};

function ToastItem({ id, message, type, duration = 5000, action, onDismiss }: ToastItemProps) {
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

  const Icon = toastIcons[type];

  return (
    <div
      className={`relative flex items-start gap-3 px-4 py-3 rounded-none border min-w-[300px] max-w-[400px] animate-slide-in ${toastStyles[type]}`}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
      role="alert"
    >
      <div className="shrink-0 mt-0.5">
        <Icon />
      </div>
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
        <Icons.X />
      </button>

      {/* Progress bar */}
      <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-black/10 rounded-b-lg overflow-hidden">
        <div
          className="h-full bg-current opacity-30 transition-none"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

// Toast container and hook
let toastListeners: Array<(toast: Toast) => void> = [];

export function showToast(toast: Omit<Toast, "id">) {
  const id = Math.random().toString(36).substring(2, 9);
  toastListeners.forEach((listener) =>
    listener({ ...toast, id })
  );
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

export function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const handleToast = (toast: Toast) => {
      setToasts((prev) => [...prev, toast]);
    };

    toastListeners.push(handleToast);
    return () => {
      toastListeners = toastListeners.filter((l) => l !== handleToast);
    };
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <div key={toast.id} className="pointer-events-auto">
          <ToastItem {...toast} onDismiss={dismissToast} />
        </div>
      ))}
    </div>
  );
}
