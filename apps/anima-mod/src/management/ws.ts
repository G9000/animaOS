import { Elysia } from "elysia";

export type ModEvent =
  | { type: "mod:status"; modId: string; status: string; error?: string }
  | { type: "mod:message"; modId: string; count: number };

const subscribers = new Set<(event: ModEvent) => void>();

export function broadcastModEvent(event: ModEvent): void {
  for (const sub of subscribers) {
    try {
      sub(event);
    } catch {
      // ignore failed subscribers
    }
  }
}

export function createWsRouter(): Elysia {
  return new Elysia().ws("/api/events", {
    open(ws) {
      const handler = (event: ModEvent) => {
        ws.send(JSON.stringify(event));
      };
      subscribers.add(handler);
      (ws as any).__modHandler = handler;
    },
    close(ws) {
      const handler = (ws as any).__modHandler;
      if (handler) subscribers.delete(handler);
    },
    message() {
      // Client doesn't send messages — this is a push-only channel
    },
  });
}
