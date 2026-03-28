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
  /** Fired after a successful reconnection (not the initial connect). */
  onReconnect?: () => void;
}

const MAX_RECONNECT_ATTEMPTS = 10;

export class ConnectionManager {
  private ws: WebSocket | null = null;
  private status: ConnectionStatus = "disconnected";
  private config: AnimusConfig;
  private events: ConnectionEvents;
  private toolSchemas: ToolSchema[];
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private intentionallyClosed = false;
  private hasConnectedBefore = false;
  /** Messages queued while WebSocket was not open. */
  private sendQueue: ClientMessage[] = [];

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
    this.reconnectAttempt = 0;
    this.setStatus("connecting");

    const wsUrl = this.config.serverUrl.endsWith("/ws/agent")
      ? this.config.serverUrl
      : `${this.config.serverUrl}/ws/agent`;

    this.ws = new WebSocket(wsUrl);

    this.ws.on("open", () => {
      this.setStatus("authenticating");
      this.ws!.send(
        JSON.stringify({
          type: "auth",
          unlockToken: this.config.unlockToken,
          username: this.config.username,
        } satisfies AuthMessage),
      );
    });

    this.ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString()) as ServerMessage;

        if (msg.type === "auth_ok") {
          const isReconnect = this.hasConnectedBefore;
          this.hasConnectedBefore = true;
          this.setStatus("connected");
          this.reconnectAttempt = 0;
          // Register tools with the server
          this.send({ type: "tool_schemas", tools: this.toolSchemas });
          // Flush any queued messages
          this.flushQueue();
          if (isReconnect) {
            this.events.onReconnect?.();
          }
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

  /**
   * Send a message. If the WebSocket is not open, the message is queued
   * and will be flushed automatically when the connection is restored.
   * Returns true if sent immediately, false if queued.
   */
  send(message: ClientMessage): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(message));
      return true;
    }
    this.sendQueue.push(message);
    return false;
  }

  disconnect(): void {
    this.intentionallyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.sendQueue = [];
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

  private flushQueue(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    const queue = this.sendQueue;
    this.sendQueue = [];
    for (const msg of queue) {
      this.ws.send(JSON.stringify(msg));
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
      this.events.onError(
        new Error(`Failed to reconnect after ${MAX_RECONNECT_ATTEMPTS} attempts. Use /reconnect or restart.`),
      );
      return;
    }
    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempt), 30000);
    this.reconnectAttempt++;
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }
}
