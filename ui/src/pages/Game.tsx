import { useEffect, useMemo, useRef, useState } from "react";
import type { Session } from "../App";
import { api, type RoomState, type Clue } from "../api";
import { subscribe, type ServerEvent } from "../realtime";

interface Props {
  session: Session;
  onLeave: () => void;
}

interface ChatLine {
  seq: number;
  role: "player" | "story" | "system" | "self-clue" | "accuse" | "win";
  who?: string;
  text: string;
  clues?: Clue[];
}

export function Game({ session, onLeave }: Props) {
  const [state, setState] = useState<RoomState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatLine[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [starting, setStarting] = useState(false);
  const [accusing, setAccusing] = useState<string | null>(null);
  const chatBottom = useRef<HTMLDivElement>(null);

  // Initial snapshot
  useEffect(() => {
    let cancelled = false;
    api.getState(session.code, session.token)
      .then((s) => { if (!cancelled) setState(s); })
      .catch((e) => { if (!cancelled) setError(String(e.message ?? e)); });
    return () => { cancelled = true; };
  }, [session.code, session.token]);

  // Realtime subscription
  useEffect(() => {
    if (!state) return;
    const sub = subscribe(
      session.code,
      session.token,
      0, // we already loaded state; events with seq <= our snapshot get filtered out by the consumer below
      (ev) => handleEvent(ev),
      (err) => console.warn("realtime error", err),
    );
    return () => sub.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.code, session.token, !!state]);

  useEffect(() => {
    chatBottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat.length]);

  function handleEvent(ev: ServerEvent) {
    // Refresh state on big transitions
    if (ev.kind === "start" || ev.kind === "win" || ev.kind === "join") {
      api.getState(session.code, session.token).then(setState).catch(() => {});
    }
    setChat((prev) => {
      // De-dupe by seq
      if (prev.some((l) => l.seq === ev.seq)) return prev;
      const next = [...prev];
      switch (ev.kind) {
        case "message": {
          const role = ev.payload.role === "system" ? "system" : "player";
          next.push({
            seq: ev.seq, role: role as ChatLine["role"],
            who: ev.payload.name, text: ev.payload.text,
          });
          break;
        }
        case "story":
          next.push({ seq: ev.seq, role: "story", who: ev.payload.name, text: ev.payload.text });
          break;
        case "clue":
          next.push({
            seq: ev.seq, role: "self-clue",
            text: `You uncovered ${ev.payload.clues.length} clue(s): +${ev.payload.points_awarded} pts`,
            clues: ev.payload.clues,
          });
          break;
        case "accuse":
          next.push({
            seq: ev.seq, role: "accuse",
            text: ev.payload.status === "wrong"
              ? `${ev.payload.player_name} wrongly accused ${ev.payload.suspect_name} (-${ev.payload.penalty} pts).`
              : `Accusation result: ${ev.payload.status}`,
          });
          break;
        case "win":
          next.push({
            seq: ev.seq, role: "win",
            text: `🏆 ${ev.payload.player_name} correctly accused ${ev.payload.suspect_name}! Motive: ${ev.payload.motive}`,
          });
          break;
        case "join":
          next.push({
            seq: ev.seq, role: "system",
            text: `${ev.payload.name} joined the room.`,
          });
          break;
        case "start":
          next.push({
            seq: ev.seq, role: "system",
            text: `The mystery "${ev.payload.title}" has begun.`,
          });
          break;
        default:
          break;
      }
      return next;
    });
  }

  async function sendMessage() {
    if (!input.trim() || sending) return;
    const text = input.trim();
    setInput("");
    setSending(true);
    try {
      await api.message(session.code, session.token, text);
      // Server broadcasts everything via SSE, so no local push needed
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setSending(false);
    }
  }

  async function startGame() {
    setStarting(true);
    setError(null);
    try {
      await api.start(session.code, session.token);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setStarting(false);
    }
  }

  async function accuse(suspect_id: string, suspect_name: string) {
    if (!confirm(`Accuse ${suspect_name}? Wrong = -10 pts. Right = win.`)) return;
    setAccusing(suspect_id);
    try {
      await api.accuse(session.code, session.token, suspect_id);
      const s = await api.getState(session.code, session.token);
      setState(s);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setAccusing(null);
    }
  }

  if (error && !state) {
    return (
      <div style={styles.shell}>
        <div style={{ color: "var(--danger)", padding: 24 }}>
          Error: {error} <button onClick={onLeave} style={styles.smallBtn}>Leave</button>
        </div>
      </div>
    );
  }

  if (!state) {
    return <div style={styles.shell}><div style={{ padding: 24 }}>Loading room {session.code}…</div></div>;
  }

  const youName = state.you?.name ?? "you";
  const isHost = state.host_id === session.playerId;

  return (
    <div style={styles.shell}>
      <header style={styles.header}>
        <div>
          <h1 style={styles.gameTitle}>{state.mystery?.title ?? `Room ${state.code}`}</h1>
          {state.mystery && <div style={styles.setting}>{state.mystery.setting}</div>}
        </div>
        <div style={styles.headerRight}>
          <div>Room <code style={styles.roomCode}>{state.code}</code></div>
          <div style={styles.status}>Status: <b>{state.status}</b></div>
          <button onClick={onLeave} style={styles.smallBtn}>Leave</button>
        </div>
      </header>

      {state.status === "lobby" && (
        <Lobby
          players={state.players}
          isHost={isHost}
          starting={starting}
          onStart={startGame}
          code={state.code}
          error={error}
        />
      )}

      {state.status !== "lobby" && state.mystery && (
        <div style={styles.grid}>
          <ChatPanel
            lines={chat}
            youName={youName}
            input={input}
            setInput={setInput}
            sending={sending || state.status === "over"}
            onSend={sendMessage}
            chatBottomRef={chatBottom}
            status={state.status}
          />
          <MysteryPanel state={state} />
          <SidePanel
            state={state}
            onAccuse={accuse}
            accusing={accusing}
            isOver={state.status === "over"}
          />
        </div>
      )}

      {error && state.status !== "lobby" && (
        <div style={styles.errorBar}>{error}</div>
      )}
    </div>
  );
}

function Lobby({ players, isHost, starting, onStart, code, error }: any) {
  return (
    <div style={styles.lobby}>
      <h2 style={{ marginBottom: 4 }}>Waiting to start</h2>
      <p style={{ color: "var(--muted)" }}>
        Share the room code <code style={styles.roomCodeBig}>{code}</code> with other players.
      </p>
      <h3>Players in the room</h3>
      <ul style={styles.playerList}>
        {players.map((p: any) => (
          <li key={p.id} style={styles.playerRow}>
            <span>{p.name}</span>
            <span style={{ color: "var(--muted)" }}>{p.points} pts</span>
          </li>
        ))}
      </ul>
      {isHost ? (
        <button
          onClick={onStart}
          disabled={starting}
          style={starting ? styles.buttonDisabled : styles.button}
        >
          {starting ? "Generating mystery…" : "Start the game"}
        </button>
      ) : (
        <p style={{ color: "var(--muted)" }}>Waiting for the host to start the game…</p>
      )}
      {error && <div style={styles.errorBar}>{error}</div>}
    </div>
  );
}

function ChatPanel({ lines, input, setInput, sending, onSend, chatBottomRef, status }: any) {
  return (
    <div style={styles.chatPanel}>
      <div style={styles.panelTitle}>Storyteller</div>
      <div style={styles.chatLog}>
        {lines.map((l: ChatLine) => (
          <ChatLineView key={l.seq} line={l} />
        ))}
        <div ref={chatBottomRef} />
      </div>
      <div style={styles.chatInputBar}>
        <input
          style={styles.chatInput}
          value={input}
          disabled={sending || status === "over"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSend()}
          placeholder={
            status === "over" ? "Game over." :
            "Ask the storyteller… (specific = better)"
          }
        />
        <button
          onClick={onSend}
          disabled={sending || !input.trim() || status === "over"}
          style={sending || !input.trim() || status === "over" ? styles.buttonDisabledSm : styles.buttonSm}
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}

function ChatLineView({ line }: { line: ChatLine }) {
  const base = styles.chatLine;
  switch (line.role) {
    case "player":
      return (
        <div style={{ ...base, ...styles.linePlayer }}>
          <b>{line.who}:</b> {line.text}
        </div>
      );
    case "story":
      return (
        <div style={{ ...base, ...styles.lineStory }}>
          <i>Storyteller{line.who ? ` (to ${line.who})` : ""}:</i> {line.text}
        </div>
      );
    case "system":
      return <div style={{ ...base, ...styles.lineSystem }}>{line.text}</div>;
    case "self-clue":
      return (
        <div style={{ ...base, ...styles.lineClue }}>
          <div><b>{line.text}</b></div>
          <ul style={{ margin: "6px 0 0 18px" }}>
            {line.clues?.map((c) => (
              <li key={c.id}>({c.points} pts) {c.text}</li>
            ))}
          </ul>
        </div>
      );
    case "accuse":
      return <div style={{ ...base, ...styles.lineAccuse }}>{line.text}</div>;
    case "win":
      return <div style={{ ...base, ...styles.lineWin }}>{line.text}</div>;
  }
}

function MysteryPanel({ state }: { state: RoomState }) {
  const m = state.mystery!;
  const youClueIds = new Set(state.you?.discovered_clue_ids ?? []);
  const cluesByScene = useMemo(() => {
    const map = new Map<string, Clue[]>();
    for (const c of (state.you?.discovered_clues ?? [])) {
      const arr = map.get(c.scene_id) ?? [];
      arr.push(c);
      map.set(c.scene_id, arr);
    }
    return map;
  }, [state.you?.discovered_clues]);

  return (
    <div style={styles.midPanel}>
      <div style={styles.panelTitle}>The case</div>
      <p><b>Victim:</b> {m.victim}</p>
      <h4 style={styles.h4}>Suspects</h4>
      <ul style={styles.suspectList}>
        {m.suspects.map((s) => (
          <li key={s.id} style={styles.suspectCard}>
            <div><b>{s.name}</b> — {s.role}</div>
            <div style={styles.muted}>{s.description}</div>
            <div style={styles.smallMuted}><i>Alibi:</i> {s.alibi}</div>
          </li>
        ))}
      </ul>
      <h4 style={styles.h4}>Scenes</h4>
      <ul style={styles.suspectList}>
        {m.scenes.map((sc) => (
          <li key={sc.id} style={styles.suspectCard}>
            <div><b>{sc.name}</b></div>
            <div style={styles.muted}>{sc.description}</div>
            {(cluesByScene.get(sc.id) ?? []).length > 0 && (
              <ul style={{ margin: "6px 0 0 16px", color: "var(--good)" }}>
                {cluesByScene.get(sc.id)!.map((c) => (
                  <li key={c.id}>({c.points} pts) {c.text}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
      <div style={{ color: "var(--muted)", marginTop: 8, fontSize: 12 }}>
        {m.clues.length} total clues. You've found {youClueIds.size}.
      </div>
    </div>
  );
}

function SidePanel({ state, onAccuse, accusing, isOver }: any) {
  return (
    <div style={styles.sidePanel}>
      <div style={styles.panelTitle}>Leaderboard</div>
      <ul style={styles.scoreList}>
        {[...state.players].sort((a: any, b: any) => b.points - a.points).map((p: any) => (
          <li key={p.id} style={styles.scoreRow}>
            <span>{p.name}{state.winner_id === p.id ? " 🏆" : ""}</span>
            <b>{p.points}</b>
          </li>
        ))}
      </ul>
      {state.mystery && !isOver && (
        <>
          <div style={{ ...styles.panelTitle, marginTop: 18 }}>Accuse</div>
          <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 0 }}>
            Used {state.you?.accusations_used ?? 0}/3. Wrong = -10. Right = +50 and win.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {state.mystery.suspects.map((s: any) => (
              <button
                key={s.id}
                onClick={() => onAccuse(s.id, s.name)}
                disabled={(state.you?.accusations_used ?? 0) >= 3 || accusing !== null}
                style={
                  (state.you?.accusations_used ?? 0) >= 3 || accusing
                    ? styles.buttonDisabledSm
                    : styles.dangerBtn
                }
              >
                {accusing === s.id ? "Accusing…" : `Accuse ${s.name}`}
              </button>
            ))}
          </div>
        </>
      )}
      {isOver && (
        <div style={styles.gameOver}>
          Game over. Refresh to start a new room.
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: { minHeight: "100vh", display: "flex", flexDirection: "column", padding: 16, gap: 12 },
  header: {
    display: "flex", justifyContent: "space-between", alignItems: "flex-start",
    padding: "8px 16px", background: "var(--panel)", borderRadius: 8,
  },
  gameTitle: { margin: 0, fontFamily: "Georgia, serif", color: "var(--accent)" },
  setting: { color: "var(--muted)", fontStyle: "italic", marginTop: 4, fontSize: 13 },
  headerRight: { textAlign: "right", display: "flex", gap: 12, alignItems: "center" },
  roomCode: { background: "#0a1018", padding: "2px 8px", borderRadius: 4, color: "var(--accent)" },
  roomCodeBig: { background: "#0a1018", padding: "6px 14px", borderRadius: 6, color: "var(--accent)", fontSize: 22, letterSpacing: 4 },
  status: { color: "var(--muted)" },
  smallBtn: {
    padding: "6px 10px", borderRadius: 4, background: "#2d3a52", color: "var(--ink)",
    border: "none", cursor: "pointer", fontSize: 12,
  },
  grid: {
    display: "grid", gridTemplateColumns: "1.2fr 1.4fr 1fr",
    gap: 12, flex: 1, minHeight: 0,
  },
  chatPanel: {
    display: "flex", flexDirection: "column",
    background: "var(--panel)", borderRadius: 8, padding: 12, minHeight: 0,
  },
  midPanel: {
    background: "var(--panel)", borderRadius: 8, padding: 12,
    overflowY: "auto",
  },
  sidePanel: {
    background: "var(--panel)", borderRadius: 8, padding: 12,
    overflowY: "auto",
  },
  panelTitle: { fontSize: 12, textTransform: "uppercase", letterSpacing: 1.5, color: "var(--muted)", marginBottom: 8 },
  chatLog: { flex: 1, overflowY: "auto", paddingRight: 4 },
  chatLine: { padding: "6px 8px", borderRadius: 4, marginBottom: 4, lineHeight: 1.4 },
  linePlayer: { background: "rgba(255,255,255,0.03)" },
  lineStory: { background: "rgba(212,160,78,0.06)", borderLeft: "2px solid var(--accent)" },
  lineSystem: { color: "var(--muted)", fontStyle: "italic", textAlign: "center" },
  lineClue: { background: "rgba(111,197,156,0.10)", borderLeft: "2px solid var(--good)" },
  lineAccuse: { background: "rgba(225,107,107,0.10)", borderLeft: "2px solid var(--danger)" },
  lineWin: { background: "rgba(212,160,78,0.20)", border: "1px solid var(--accent)", padding: 10, textAlign: "center", fontSize: 15 },
  chatInputBar: { display: "flex", gap: 8, marginTop: 8 },
  chatInput: {
    flex: 1, padding: "10px 12px", borderRadius: 4, border: "1px solid #2d3a52",
    background: "#0f1520", color: "var(--ink)",
  },
  button: {
    padding: "10px 16px", borderRadius: 6, background: "var(--accent)", color: "#1a1410",
    fontWeight: 600, border: "none", cursor: "pointer",
  },
  buttonDisabled: {
    padding: "10px 16px", borderRadius: 6, background: "#3a4256", color: "#7a8298",
    fontWeight: 600, border: "none", cursor: "not-allowed",
  },
  buttonSm: {
    padding: "8px 14px", borderRadius: 4, background: "var(--accent)", color: "#1a1410",
    fontWeight: 600, border: "none", cursor: "pointer",
  },
  buttonDisabledSm: {
    padding: "8px 14px", borderRadius: 4, background: "#3a4256", color: "#7a8298",
    border: "none", cursor: "not-allowed",
  },
  dangerBtn: {
    padding: "8px 12px", borderRadius: 4, background: "rgba(225,107,107,0.15)",
    color: "var(--danger)", border: "1px solid var(--danger)", cursor: "pointer",
    textAlign: "left",
  },
  h4: { marginTop: 14, marginBottom: 6, color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: 1 },
  suspectList: { listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 },
  suspectCard: {
    padding: 8, background: "var(--panel-2)", borderRadius: 4, fontSize: 13, lineHeight: 1.4,
  },
  muted: { color: "var(--muted)" },
  smallMuted: { color: "var(--muted)", fontSize: 12, marginTop: 4 },
  scoreList: { listStyle: "none", padding: 0, margin: 0 },
  scoreRow: {
    display: "flex", justifyContent: "space-between", padding: "6px 8px",
    borderBottom: "1px solid #232c3d",
  },
  gameOver: {
    marginTop: 18, padding: 12, background: "rgba(212,160,78,0.10)", borderRadius: 4,
    color: "var(--accent)", textAlign: "center",
  },
  lobby: {
    background: "var(--panel)", borderRadius: 8, padding: 24, maxWidth: 600, margin: "32px auto",
  },
  playerList: { listStyle: "none", padding: 0, margin: "8px 0 24px" },
  playerRow: {
    display: "flex", justifyContent: "space-between",
    padding: "8px 12px", background: "var(--panel-2)", borderRadius: 4, marginBottom: 4,
  },
  errorBar: {
    background: "rgba(225,107,107,0.20)", color: "var(--danger)",
    padding: 10, borderRadius: 4, marginTop: 8,
  },
};
