// Realtime wrapper. Default: SSE via EventSource. If SSE is broken on the host runtime,
// flip USE_LONG_POLL to true (or set VITE_USE_LONG_POLL=1 at build time) and the same
// subscribe() signature works.

import { getApiBase } from "./api";

export interface ServerEvent {
  seq: number;
  ts: number;
  kind: string;
  payload: Record<string, any>;
}

export type EventHandler = (event: ServerEvent) => void;

const USE_LONG_POLL = import.meta.env.VITE_USE_LONG_POLL === "1";

export interface Subscription {
  close: () => void;
}

export function subscribe(
  code: string,
  token: string | undefined,
  fromSeq: number,
  onEvent: EventHandler,
  onError?: (err: Error) => void,
): Subscription {
  if (USE_LONG_POLL) {
    return subscribePoll(code, token, fromSeq, onEvent, onError);
  }
  return subscribeSse(code, token, fromSeq, onEvent, onError);
}

function subscribeSse(
  code: string,
  token: string | undefined,
  fromSeq: number,
  onEvent: EventHandler,
  onError?: (err: Error) => void,
): Subscription {
  let es: EventSource | null = null;
  let closed = false;

  async function open() {
    const base = await getApiBase();
    if (closed) return;
    const qs = new URLSearchParams();
    if (token) qs.set("token", token);
    if (fromSeq > 0) qs.set("since", String(fromSeq));
    es = new EventSource(`${base}/rooms/${code}/events?${qs}`);
    es.onmessage = (e) => handle(e);
    // Custom event types we publish (join, start, clue, message, accuse, win, story, leave)
    for (const k of [
      "join", "start", "clue", "clue_found", "message", "accuse", "win", "story", "leave",
      "suspect_image", "narration_chunk", "narration_end",
    ]) {
      es.addEventListener(k, (e) => handle(e as MessageEvent));
    }
    es.onerror = () => {
      if (closed) return;
      onError?.(new Error("SSE connection error"));
      // EventSource auto-reconnects; nothing else to do
    };
  }

  function handle(e: MessageEvent) {
    try {
      const data = JSON.parse(e.data);
      onEvent(data);
    } catch {
      // ignore malformed frames
    }
  }

  open();

  return {
    close() {
      closed = true;
      es?.close();
    },
  };
}

function subscribePoll(
  code: string,
  token: string | undefined,
  fromSeq: number,
  onEvent: EventHandler,
  onError?: (err: Error) => void,
): Subscription {
  let closed = false;
  let since = fromSeq;

  (async function loop() {
    const base = await getApiBase();
    while (!closed) {
      try {
        const qs = new URLSearchParams({ since: String(since), wait: "20" });
        if (token) qs.set("token", token);
        const res = await fetch(`${base}/rooms/${code}/events_poll?${qs}`);
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const j = await res.json();
        for (const e of j.events as ServerEvent[]) {
          onEvent(e);
          if (e.seq > since) since = e.seq;
        }
        if (j.next_seq > since) since = j.next_seq - 1;
      } catch (err) {
        if (!closed) onError?.(err as Error);
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
  })();

  return {
    close() {
      closed = true;
    },
  };
}
