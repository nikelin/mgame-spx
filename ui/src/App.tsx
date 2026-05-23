import { useEffect, useState } from "react";
import { Home } from "./pages/Home";
import { Game } from "./pages/Game";

export interface Session {
  code: string;
  playerId: string;
  token: string;
  isHost: boolean;
}

const SESSION_KEY = "spgame.session";

function loadSession(): Session | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function saveSession(s: Session | null) {
  if (s) sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
  else sessionStorage.removeItem(SESSION_KEY);
}

export function App() {
  const [session, setSession] = useState<Session | null>(loadSession);

  useEffect(() => {
    saveSession(session);
  }, [session]);

  if (!session) {
    return <Home onJoined={(s) => setSession(s)} />;
  }
  return <Game session={session} onLeave={() => setSession(null)} />;
}
