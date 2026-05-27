// Runtime API base loaded from /config.json (served by the ui-host FastAPI app).
// Falls back to same-origin for dev (vite proxies) and for the single-deploy case.

let _apiBase: string | null = null;

export async function getApiBase(): Promise<string> {
  if (_apiBase !== null) return _apiBase;
  try {
    const res = await fetch("/config.json", { cache: "no-store" });
    if (res.ok) {
      const j = await res.json();
      _apiBase = (j.apiBase || "").replace(/\/$/, "");
      return _apiBase!;
    }
  } catch {
    // ignore — fall through
  }
  _apiBase = "";
  return _apiBase;
}

// Synchronous accessor for already-cached apiBase. Used to build asset URLs (e.g. portrait
// paths) inside React renders — apiBase is loaded on first API call so by the time the
// game UI renders portraits, it's already cached.
export function apiBaseSync(): string {
  return _apiBase ?? "";
}

export function resolveAssetUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^(https?:|data:)/i.test(url)) return url;
  return apiBaseSync() + url;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const base = await getApiBase();
  const res = await fetch(`${base}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j.detail) detail = j.detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json();
}

export interface Suspect {
  id: string; name: string; role: string; description: string; alibi: string;
  image_url?: string | null;
}
export interface Scene { id: string; name: string; description: string; }
export interface ClueMeta { id: string; scene_id: string; points: number; }
export interface Clue {
  id: string; text: string; points: number; scene_id: string;
  image_url?: string | null;
  image_title?: string | null;
}

// Public summary visible to all players (clue text is omitted)
export interface ClueSummary {
  clue_id: string;
  image_url?: string | null;
  image_title?: string | null;
  scene_id?: string | null;
  points?: number | null;
}

export interface PublicMystery {
  title: string;
  setting: string;
  victim: string;
  suspects: Suspect[];
  scenes: Scene[];
  clues: ClueMeta[];
  culprit_id?: string;
  motive?: string;
}

export interface PlayerView {
  id: string;
  name: string;
  points: number;
  accusations_used: number;
  discovered_clue_ids: string[];
  discovered_clues: Clue[];
  is_host: boolean;
}

export interface AccusationLogEntry {
  ts: number;
  player_id: string;
  player_name: string;
  suspect_id: string;
  suspect_name: string;
  correct: boolean;
}

export interface ServerEventSnapshot {
  seq: number;
  ts: number;
  kind: string;
  payload: Record<string, any>;
}

export interface RoomState {
  code: string;
  status: "lobby" | "playing" | "over";
  host_id: string;
  winner_id: string | null;
  mystery: PublicMystery | null;
  players: { id: string; name: string; points: number; accusations_used: number }[];
  next_seq: number;
  you?: PlayerView;
  finds_by_player?: Record<string, ClueSummary[]>;
  narration?: string;
  narration_done?: boolean;
  accusation_log?: AccusationLogEntry[];
  chat_events?: ServerEventSnapshot[];
}

export type Language = "en" | "pt";

export const api = {
  createRoom: (host_name: string, openai_api_key?: string | null, language?: Language) => {
    const body: Record<string, unknown> = { host_name };
    if (openai_api_key) body.openai_api_key = openai_api_key;
    if (language) body.language = language;
    return request<{
      code: string; player_id: string; token: string;
      is_host: boolean; uses_custom_key: boolean; language: Language;
    }>("POST", "/rooms", body);
  },
  // Join is idempotent for the same name (case-insensitive): a returning player gets
  // back their existing player_id and a (re-issued) token. Acts as a resume entry point.
  joinRoom: (code: string, name: string) =>
    request<{ code: string; player_id: string; token: string; is_host: boolean }>(
      "POST", `/rooms/${code}/join`, { name },
    ),
  getState: (code: string, token?: string) =>
    request<RoomState>("GET", `/rooms/${code}/state${token ? `?token=${encodeURIComponent(token)}` : ""}`),
  start: (code: string, token: string, theme?: string) =>
    request<{ status: string; title: string }>(
      "POST", `/rooms/${code}/start`, { token, theme: theme || null },
    ),
  message: (code: string, token: string, text: string) =>
    request<{ reply: string; revealed_clues: Clue[]; points: number; story_progress_bonus: number }>(
      "POST", `/rooms/${code}/message`, { token, text },
    ),
  accuse: (code: string, token: string, suspect_id: string) =>
    request<Record<string, any>>("POST", `/rooms/${code}/accuse`, { token, suspect_id }),
};
