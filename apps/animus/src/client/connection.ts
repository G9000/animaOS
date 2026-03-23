// apps/animus/src/client/connection.ts
import WebSocket from "ws";
import type {
  AuthMessage,
  ClientMessage,
  ServerMessage,
  ToolSchema,
} from "./protocol";
import { type AnimusConfig, getConfigPath } from "./auth";
import { unlinkSync } from "node:fs";

export type ConnectionStatus =
  | "disconnected"
  | "connecting"
  | "authenticating"
  | "connected";

export interface ConnectionEvents {
  onStatusChange: (status: ConnectionStatus) => void;
  onMessage: (message: ServerMessage) => void;
  onError: (error: Error) => void;
}

export class ConnectionManager {
  private ws: WebSocket | null = null;
  private status: ConnectionStatus = "disconnected";
  private config: AnimusConfig;
  private events: ConnectionEvents;
  private toolSchemas: ToolSchema[];
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionallyClosed = false;

  constructor(
    config: AnimusConfig,
    toolSchemas: ToolSchema[],
    events: ConnectionEvents,
  ) {
    this.config = config;
    this.toolSchemas = toolSchemas;
    this.events = events;
  }

  connect(): void {
    this.intentionallyClosed = false;
    this.setStatus("connecting");

    const wsUrl = this.config.serverUrl.endsWith("/ws/agent")
      ? this.config.serverUrl
      : `${this.config.serverUrl}/ws/agent`;

    this.ws = new WebSocket(wsUrl);

    this.ws.on("open", () => {
      this.setStatus("authenticating");
      this.send({
        type: "auth",
        unlockToken: this.config.unlockToken,
        username: this.config.username,
      } satisfies AuthMessage);
    });

    this.ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString()) as ServerMessage;

        if (msg.type === "auth_ok") {
          this.setStatus("connected");
          this.reconnectAttempt = 0;
          // Register tools with the server
          this.send({ type: "tool_schemas", tools: this.toolSchemas });
        }

        // Auth failure — stop reconnecting, delete stale config
        if (
          msg.type === "error" &&
          (msg.code === "AUTH_FAILED" || msg.code === "AUTH_REQUIRED")
        ) {
          this.intentionallyClosed = true;
          try {
            unlinkSync(getConfigPath());
          } catch {}
          this.events.onError(
            new Error(`${msg.message}. Saved config cleared — restart to re-login.`),
          );
          return;
        }

        this.events.onMessage(msg);
      } catch (err) {
        this.events.onError(new Error(`Failed to parse message: ${err}`));
      }
    });

    this.ws.on("close", () => {
      this.setStatus("disconnected");
      if (!this.intentionallyClosed) {
        this.scheduleReconnect();
      }
    });

    this.ws.on("error", (err) => {
      this.events.onError(
        err instanceof Error ? err : new Error(String(err)),
      );
    });
  }

  send(message: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
    }
  }

  disconnect(): void {
    this.intentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setStatus("disconnected");
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.events.onStatusChange(status);
  }

  private scheduleReconnect(): void {
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempt), 30000);
    this.reconnectAttempt++;
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }
}
