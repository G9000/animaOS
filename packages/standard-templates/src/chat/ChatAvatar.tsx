import { UserIcon } from "./icons";
import { cn } from "../utils/cn";
import type { MessageRole } from "./types";

export interface ChatAvatarProps {
  role: MessageRole;
  avatarUrl?: string;
  className?: string;
  size?: "sm" | "md" | "lg";
}

const sizeClasses = {
  sm: "w-6 h-6",
  md: "w-8 h-8",
  lg: "w-10 h-10",
};

const iconSizes = {
  sm: "w-4 h-4",
  md: "w-4 h-4",
  lg: "w-5 h-5",
};

export function ChatAvatar({
  role,
  avatarUrl,
  className,
  size = "md",
}: ChatAvatarProps) {
  if (role === "user") {
    return (
      <div
        className={cn(
          sizeClasses[size],
          "bg-primary/20 ring-2 ring-primary/30 flex items-center justify-center",
          className,
        )}
      >
        <UserIcon className={cn(iconSizes[size], "text-primary/70")} />
      </div>
    );
  }

  // Assistant or system
  if (avatarUrl) {
    return (
      <img
        src={avatarUrl}
        alt="Agent"
        className={cn(
          sizeClasses[size],
          "ring-2 ring-primary/15 object-cover",
          className,
        )}
      />
    );
  }

  // Fallback for assistant without avatar
  return (
    <div
      className={cn(
        sizeClasses[size],
        "bg-primary/10 ring-2 ring-primary/20 flex items-center justify-center",
        className,
      )}
    >
      <span className="font-mono text-[10px] text-primary/60">AI</span>
    </div>
  );
}
