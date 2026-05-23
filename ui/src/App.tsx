import { useEffect, useState } from "react";
import { Home } from "./pages/Home";
import { Game } from "./pages/Game";
import { api } from "./api";

export interface Session {
  code: string;
  playerId: string;
  token: string;
  isHost: boolean;
  // Name is the canonical identity — keeping it in storage lets us auto-rejoin if the
  // token is rejected (e.g., server restarted with a different token issuance).
  name: string;
}

// Persist in localStorage so a full browser restart still rejoins; sessionStorage only
// survives reloads within the same tab.
const SESSION_KEY = "spgame.session.v2";

function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY) ?? sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveSession(s: Session | null) {
  if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
  else localStorage.removeItem(SESSION_KEY);
  sessionStorage.removeItem(SESSION_KEY);
}

export function App() {
  const [session, setSession] = useState<Session | null>(loadSession);
  const [resuming, setResuming] = useState(false);

  useEffect(() => {
    saveSession(session);
  }, [session]);

  // If we have a stored session, refresh the token by re-joining with the saved name.
  // The server returns the existing player record (same id, refreshed token) when the
  // name already exists in the room. If the room is gone, fall back to the Home screen.
  useEffect(() => {
    if (!session || resuming) return;
    let cancelled = false;
    setResuming(true);
    api.joinRoom(session.code, session.name)
      .then((r) => {
        if (cancelled) return;
        if (r.token !== session.token || r.player_id !== session.playerId) {
          setSession({
            code: r.code, playerId: r.player_id, token: r.token,
            isHost: r.is_host, name: session.name,
          });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        // Room no longer exists, etc. Surface the user to the home screen.
        console.warn("rejoin failed; clearing session", err);
        setSession(null);
      })
      .finally(() => { if (!cancelled) setResuming(false); });
    return () => { cancelled = true; };
    // Run only once on initial mount with a stored session; subsequent setSession calls
    // come from Home or onLeave.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!session) {
    return <Home onJoined={(s) => setSession(s)} />;
  }
  return <Game session={session} onLeave={() => setSession(null)} />;
}
