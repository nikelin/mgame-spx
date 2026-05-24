import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Session } from "../App";
import { api, apiBaseSync, resolveAssetUrl, type AccusationLogEntry, type RoomState, type Clue, type ClueSummary, type Suspect } from "../api";
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
  const [portraits, setPortraits] = useState<Record<string, string>>({});
  const [narration, setNarration] = useState<string>("");
  const [narrationDone, setNarrationDone] = useState<boolean>(false);
  // Per-player public clue summaries: player_id → list of {clue_id, image, title}
  const [foundByPlayer, setFoundByPlayer] = useState<Record<string, ClueSummary[]>>({});
  // Full clue text only for clues YOU discovered: clue_id → Clue
  const [myClueText, setMyClueText] = useState<Record<string, Clue>>({});
  // Ordered accusation log: append entries as accuse/win events arrive, hydrate from /state.
  const [accusations, setAccusations] = useState<AccusationLogEntry[]>([]);
  // Scroll target is the chat log container itself (not a sentinel + scrollIntoView, which
  // would also scroll the page on short conversations).
  const chatLogRef = useRef<HTMLDivElement>(null);

  // Initial snapshot
  useEffect(() => {
    let cancelled = false;
    api.getState(session.code, session.token)
      .then((s) => {
        if (cancelled) return;
        setState(s);
        // Hydrate per-player finds and self-clues from the snapshot so reload mid-game
        // doesn't lose the right-panel data.
        if (s.finds_by_player) setFoundByPlayer(s.finds_by_player);
        if (s.you?.discovered_clues) {
          const map: Record<string, Clue> = {};
          for (const c of s.you.discovered_clues) map[c.id] = c as Clue;
          setMyClueText(map);
        }
        // Hydrate the opening narration. The streamed events that produced it may have
        // happened in a prior process (server restart, redeploy) — pick up the full text
        // from the persisted snapshot here.
        if (s.narration) setNarration(s.narration);
        if (s.narration_done) setNarrationDone(true);
        // Hydrate suspect portraits from the public mystery view, in case the suspect_image
        // events fired in a prior process.
        if (s.mystery?.suspects) {
          const map: Record<string, string> = {};
          for (const sp of s.mystery.suspects) {
            if (sp.image_url) map[sp.id] = sp.image_url;
          }
          setPortraits((prev) => ({ ...map, ...prev }));
        }
        if (s.accusation_log) setAccusations(s.accusation_log);
        // Replay the historical chat events so the chat panel rebuilds after reload.
        // handleEvent already dedupes by seq, so when the live SSE stream catches up we
        // won't show anything twice.
        if (s.chat_events) {
          for (const e of s.chat_events) handleEvent(e as ServerEvent, true);
        }
      })
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
    const el = chatLogRef.current;
    if (!el) return;
    // Scroll only the chat log to its bottom; never propagate to the page/window.
    // No-op when the content fits within the visible area.
    if (el.scrollHeight > el.clientHeight) {
      el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    }
  }, [chat.length]);

  function handleEvent(ev: ServerEvent, isHistorical: boolean = false) {
    // Refresh state on big transitions — but skip during replay; the snapshot we're
    // replaying from is already the freshest state we have.
    if (!isHistorical && (ev.kind === "start" || ev.kind === "win" || ev.kind === "join")) {
      api.getState(session.code, session.token).then(setState).catch(() => {});
    }
    if (ev.kind === "suspect_image" && ev.payload.suspect_id && ev.payload.image_url) {
      setPortraits((prev) => ({ ...prev, [ev.payload.suspect_id]: ev.payload.image_url }));
      return; // suspect_image isn't a chat-worthy event
    }
    if (ev.kind === "narration_chunk") {
      setNarration((prev) => prev + (ev.payload.text ?? ""));
      return;
    }
    if (ev.kind === "narration_end") {
      setNarrationDone(true);
      return;
    }
    if (ev.kind === "clue_found") {
      const pid = ev.payload.player_id as string;
      const newClues = (ev.payload.clues as ClueSummary[]) ?? [];
      setFoundByPlayer((prev) => {
        const existing = prev[pid] ?? [];
        const have = new Set(existing.map((c) => c.clue_id));
        const merged = [...existing, ...newClues.filter((c) => !have.has(c.clue_id))];
        return { ...prev, [pid]: merged };
      });
      // Fall through to chat log handling below — also produces the "X uncovered N" line
    }
    if (ev.kind === "clue") {
      // Private — only the discovering player sees this. Cache full text by clue id.
      const clues = (ev.payload.clues as Clue[]) ?? [];
      setMyClueText((prev) => {
        const next = { ...prev };
        for (const c of clues) next[c.id] = c;
        return next;
      });
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
        case "clue_found":
          next.push({
            seq: ev.seq, role: "system",
            text: `${ev.payload.name} uncovered ${ev.payload.clues.length} clue(s) (+${ev.payload.points_awarded} pts).`,
          });
          break;
        case "accuse":
          next.push({
            seq: ev.seq, role: "accuse",
            text: ev.payload.status === "wrong"
              ? `${ev.payload.player_name} wrongly accused ${ev.payload.suspect_name} (-${ev.payload.penalty} pts).`
              : `Accusation result: ${ev.payload.status}`,
          });
          if (ev.payload.suspect_id && ev.payload.player_name) {
            setAccusations((prev) => [...prev, {
              ts: ev.ts, player_id: ev.payload.player_id, player_name: ev.payload.player_name,
              suspect_id: ev.payload.suspect_id, suspect_name: ev.payload.suspect_name,
              correct: ev.payload.status === "correct",
            }]);
          }
          break;
        case "win":
          next.push({
            seq: ev.seq, role: "win",
            text: `🏆 ${ev.payload.player_name} correctly accused ${ev.payload.suspect_name}! Motive: ${ev.payload.motive}`,
          });
          if (ev.payload.suspect_id && ev.payload.player_name) {
            setAccusations((prev) => [...prev, {
              ts: ev.ts, player_id: ev.payload.player_id, player_name: ev.payload.player_name,
              suspect_id: ev.payload.suspect_id, suspect_name: ev.payload.suspect_name,
              correct: true,
            }]);
          }
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
          <HeaderShareButton code={state.code} kind="player" />
          <HeaderShareButton code={state.code} kind="llm" />
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
            chatLogRef={chatLogRef}
            status={state.status}
            suspects={state.mystery.suspects}
          />
          <MysteryPanel
            state={state}
            portraits={portraits}
            narration={narration}
            narrationDone={narrationDone}
            accusations={accusations}
          />
          <SidePanel
            state={state}
            onAccuse={accuse}
            accusing={accusing}
            isOver={state.status === "over"}
            portraits={portraits}
            foundByPlayer={foundByPlayer}
            myClueText={myClueText}
            youPlayerId={state.you?.id ?? null}
            scenes={state.mystery.scenes}
            suspects={state.mystery.suspects}
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

      <SharePanel code={code} />

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

// Compact "copy URL" button used in the page header so users can grab the player or LLM
// invite link without scrolling back to the lobby. The two-kind props keep styling unified.
function HeaderShareButton({ code, kind }: { code: string; kind: "player" | "llm" }) {
  const [copied, setCopied] = useState(false);
  const url = useMemo(() => {
    if (kind === "player") {
      const u = new URL(window.location.href);
      u.searchParams.set("room", code);
      return u.toString();
    }
    return `${apiBaseSync()}/llm?room=${code}`;
  }, [code, kind]);

  async function copy() {
    try { await navigator.clipboard.writeText(url); }
    catch {
      const ta = document.createElement("textarea");
      ta.value = url; document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } finally { ta.remove(); }
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  const label = kind === "llm" ? "LLM link" : "Player link";
  return (
    <button
      onClick={copy}
      style={kind === "llm" ? styles.headerShareLlm : styles.headerSharePlayer}
      title={url}
    >
      {copied ? "Copied ✓" : `Copy ${label}`}
    </button>
  );
}

function SharePanel({ code }: { code: string }) {
  // Player URL: this UI's origin + ?room=CODE — recipients land on the join page with the
  // code pre-filled and only need to enter their name.
  const playerUrl = useMemo(() => {
    const u = new URL(window.location.href);
    u.searchParams.set("room", code);
    return u.toString();
  }, [code]);
  // LLM URL: server-side endpoint that returns a Markdown briefing pre-loaded with the
  // room code, plus curl examples. Paste this into Claude / ChatGPT and the model can
  // join + play from inside the chat. /llm (briefing) is a better entry than /llm/join
  // because it teaches the LLM the full contract first.
  const llmUrl = useMemo(() => {
    const base = apiBaseSync();
    return `${base}/llm?room=${code}`;
  }, [code]);

  return (
    <div style={styles.sharePanel}>
      <div style={styles.shareTitle}>Invite others</div>

      <div style={styles.shareLabel}>Player link — for humans</div>
      <CopyableUrl url={playerUrl} />

      <div style={{ ...styles.shareLabel, marginTop: 12 }}>LLM link — paste into Claude / ChatGPT</div>
      <CopyableUrl url={llmUrl} />
      <div style={styles.shareHint}>
        The LLM URL returns a Markdown briefing with the room context and curl examples;
        replace <code>YOUR_BOT_NAME</code> with whatever you'd like the LLM-controlled player to be called.
      </div>
    </div>
  );
}

function CopyableUrl({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // Fallback if clipboard API isn't available
      const ta = document.createElement("textarea");
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); setCopied(true); setTimeout(() => setCopied(false), 1400); }
      catch { /* shrug */ }
      finally { ta.remove(); }
    }
  }
  return (
    <div style={styles.copyRow}>
      <input style={styles.copyInput} value={url} readOnly onFocus={(e) => e.target.select()} />
      <button onClick={copy} style={styles.copyBtn}>{copied ? "Copied ✓" : "Copy"}</button>
    </div>
  );
}

function ChatPanel({
  lines, input, setInput, sending, onSend, chatLogRef, status, suspects,
}: any) {
  return (
    <div style={styles.chatPanel}>
      <div style={styles.panelTitle}>Storyteller</div>
      <div ref={chatLogRef} style={styles.chatLog}>
        {lines.map((l: ChatLine) => (
          <ChatLineView key={l.seq} line={l} suspects={suspects} />
        ))}
        {/* The prompt scrolls inline with the chat history — sits right after the latest
            message rather than being pinned at the bottom of the panel. */}
        <div style={styles.chatInputBar}>
          <input
            style={styles.chatInput}
            value={input}
            disabled={sending || status === "over"}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && onSend()}
            placeholder={status === "over" ? "Game over." : "Ask the storyteller… (specific = better)"}
            autoFocus
          />
          <button
            onClick={onSend}
            disabled={sending || !input.trim() || status === "over"}
            style={(sending || !input.trim() || status === "over") ? styles.buttonDisabledSm : styles.buttonSm}
          >
            {sending ? "…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ChatLineView({ line, suspects }: { line: ChatLine; suspects: Suspect[] }) {
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
          <i>Storyteller{line.who ? ` (to ${line.who})` : ""}:</i>{" "}
          <HighlightedText text={line.text} suspects={suspects} />
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
              <li key={c.id}>({c.points} pts) <HighlightedText text={c.text} suspects={suspects} /></li>
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

// Build a regex matching any of the suspects' names (full, first, last) and wrap matches in
// highlighted spans. Longer names are tried first so "Vivien Marlowe" wins over "Vivien".
function HighlightedText({ text, suspects }: { text: string; suspects: Suspect[] }) {
  const segments = useMemo(() => splitOnSuspects(text, suspects ?? []), [text, suspects]);
  return (
    <>
      {segments.map((seg, i) =>
        seg.suspectId ? (
          <span key={i} style={styles.suspectMention} title={seg.role ?? undefined}>
            {seg.text}
          </span>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}

interface Segment { text: string; suspectId?: string; role?: string; }

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function splitOnSuspects(text: string, suspects: Suspect[]): Segment[] {
  if (!suspects.length || !text) return [{ text }];
  // For each suspect collect full name, first name, last name (de-duped).
  const tokens: { needle: string; suspectId: string; role: string }[] = [];
  for (const s of suspects) {
    const parts = s.name.split(/\s+/).filter(Boolean);
    const candidates = new Set<string>([s.name, ...parts]);
    for (const c of candidates) {
      if (c.length >= 2) tokens.push({ needle: c, suspectId: s.id, role: s.role });
    }
  }
  // Sort by length desc so multi-word names match first
  tokens.sort((a, b) => b.needle.length - a.needle.length);
  // Build a single alternation regex with word boundaries
  const pattern = new RegExp(
    `(${tokens.map((t) => escapeRegex(t.needle)).join("|")})(?!\\w)`,
    "g",
  );
  // Also require a non-word char (or start) before the match
  const startBoundary = /(?:^|[^\w])$/;
  const segs: Segment[] = [];
  let lastIdx = 0;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    const before = text.slice(lastIdx, m.index);
    if (!startBoundary.test(before)) {
      // mid-word match (e.g. "Marlowee") — skip
      continue;
    }
    if (before) segs.push({ text: before });
    const tok = tokens.find((t) => t.needle === m![0]);
    segs.push({ text: m[0], suspectId: tok?.suspectId, role: tok?.role });
    lastIdx = pattern.lastIndex;
  }
  if (lastIdx < text.length) segs.push({ text: text.slice(lastIdx) });
  return segs;
}

function MysteryPanel({
  state, portraits, narration, narrationDone, accusations,
}: {
  state: RoomState; portraits: Record<string, string>;
  narration: string; narrationDone: boolean;
  accusations: AccusationLogEntry[];
}) {
  const m = state.mystery!;
  // Narration is auto-expanded while streaming and once it finishes; the user can manually
  // collapse it to free up vertical space and refocus on suspects/scenes.
  const [narrationOpen, setNarrationOpen] = useState(true);
  // Aggregate accusations: suspect_id → list of {player_name, count}
  const accusationsBySuspect = useMemo(() => {
    const map = new Map<string, Map<string, number>>();
    for (const a of accusations) {
      const sub = map.get(a.suspect_id) ?? new Map<string, number>();
      sub.set(a.player_name, (sub.get(a.player_name) ?? 0) + 1);
      map.set(a.suspect_id, sub);
    }
    return map;
  }, [accusations]);
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
      <div style={styles.panelTitle}>The PMF hunt</div>
      <p><b>The PMF this team is chasing:</b> {m.victim}</p>

      {(narration || !narrationDone) && (
        <div style={styles.narrationBlock}>
          <button
            type="button"
            onClick={() => setNarrationOpen((o) => !o)}
            style={styles.narrationHeader}
            aria-expanded={narrationOpen}
          >
            <span style={styles.narrationTitle}>
              Case briefing {narrationDone ? "" : <span style={styles.cursor}>▌</span>}
            </span>
            <span style={styles.narrationToggle}>{narrationOpen ? "▾ hide" : "▸ show"}</span>
          </button>
          {narrationOpen && (
            narration ? (
              <div style={styles.narrationBody}>
                <HighlightedText text={narration} suspects={m.suspects} />
              </div>
            ) : (
              <div style={{ color: "var(--muted)", fontStyle: "italic", padding: "0 14px 12px" }}>
                The storyteller clears their throat…
              </div>
            )
          )}
        </div>
      )}
      <h4 style={styles.h4}>Suspects</h4>
      <ul style={styles.suspectList}>
        {m.suspects.map((s) => {
          const url = portraits[s.id] ?? s.image_url ?? null;
          const accusers = accusationsBySuspect.get(s.id);
          const accusationCount = accusers
            ? Array.from(accusers.values()).reduce((a, b) => a + b, 0)
            : 0;
          const accuserList = accusers
            ? Array.from(accusers.entries())
                .map(([name, n]) => (n > 1 ? `${name} (×${n})` : name))
                .join(", ")
            : "";
          return (
            <li key={s.id} style={{ ...styles.suspectCard, display: "flex", gap: 10 }}>
              <Portrait url={url} name={s.name} size={64} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div><b>{s.name}</b> — {s.role}</div>
                <div style={styles.muted}>{s.description}</div>
                <div style={styles.smallMuted}><i>Alibi:</i> {s.alibi}</div>
                {accusationCount > 0 && (
                  <div style={styles.accusedLine}>
                    <span style={styles.accusedBadge}>
                      Accused {accusationCount}× {accusationCount > 1 ? "times" : ""}
                    </span>
                    <span style={styles.accusedNames}> by {accuserList}</span>
                  </div>
                )}
              </div>
            </li>
          );
        })}
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

const CLUE_POP_WIDTH = 280;
const CLUE_POP_HEIGHT = 340; // approximate; used only to clamp vertical position
const CLUE_POP_GAP = 12;

function ClueChip({
  summary, fullText, sceneName, finderName, isYour,
}: {
  summary: ClueSummary;
  fullText: string | null;
  sceneName: string | null;
  finderName: string;
  isYour: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; placement: "left" | "right"; chipMidY: number } | null>(null);
  const chipRef = useRef<HTMLDivElement>(null);
  const resolved = resolveAssetUrl(summary.image_url ?? null);
  const title = summary.image_title ?? "Unknown evidence";

  const updatePosition = useCallback(() => {
    const el = chipRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    // Prefer left placement (popover sits to the left of the chip) since the chips live in
    // the right-hand panel; fall back to right if there isn't enough room.
    const placement: "left" | "right" =
      rect.left - CLUE_POP_WIDTH - CLUE_POP_GAP >= 8 ? "left" : "right";
    const left = placement === "left"
      ? rect.left - CLUE_POP_WIDTH - CLUE_POP_GAP
      : rect.right + CLUE_POP_GAP;
    // Vertically clamp so the popover fits in the viewport
    const top = Math.max(
      8,
      Math.min(rect.top, window.innerHeight - CLUE_POP_HEIGHT - 8),
    );
    setPos({ top, left, placement, chipMidY: rect.top + rect.height / 2 });
  }, []);

  // Recompute position whenever the popover opens or the page scrolls/resizes
  useEffect(() => {
    if (!open) return;
    updatePosition();
    const onScrollOrResize = () => updatePosition();
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [open, updatePosition]);

  // Click-outside closes the popover
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node;
      if (chipRef.current && chipRef.current.contains(target)) return;
      // The popover lives in a portal so we can't use ref containment; close on any other click
      const popoverEl = document.getElementById("clue-popover-active");
      if (popoverEl && popoverEl.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  return (
    <div
      ref={chipRef}
      style={styles.clueChip}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}
        style={styles.clueChipBtn}
        aria-label={`Inspect ${title}`}
      >
        {resolved ? (
          <img src={resolved} alt={title} style={styles.clueThumb} />
        ) : (
          <div style={{ ...styles.clueThumb, ...styles.clueThumbFallback }}>?</div>
        )}
        <div style={styles.clueChipLabel}>{title}</div>
      </button>

      {open && pos && createPortal(
        <div
          id="clue-popover-active"
          role="tooltip"
          style={{
            ...styles.cluePopover,
            position: "fixed",
            top: pos.top,
            left: pos.left,
          }}
          // Keep popover visible while the pointer is over it
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
        >
          <div
            style={{
              ...(pos.placement === "left" ? styles.cluePopArrowRight : styles.cluePopArrowLeft),
              top: Math.max(12, Math.min(pos.chipMidY - pos.top - 6, CLUE_POP_HEIGHT - 24)),
            }}
          />
          {resolved && (
            <img src={resolved} alt={title} style={styles.cluePopImage} />
          )}
          <div style={styles.cluePopBody}>
            <div style={styles.cluePopTitle}>{title}</div>
            <div style={styles.cluePopMeta}>
              <span>Found by <b>{finderName}</b></span>
              {summary.points != null && (
                <span style={styles.cluePopPoints}>+{summary.points} pts</span>
              )}
            </div>
            {sceneName && (
              <div style={styles.cluePopScene}>Scene: <i>{sceneName}</i></div>
            )}
            <div style={styles.cluePopText}>
              {isYour && fullText ? (
                fullText
              ) : isYour ? (
                <span style={{ color: "var(--muted)", fontStyle: "italic" }}>
                  Full description not loaded — try reopening the room.
                </span>
              ) : (
                <span style={{ color: "var(--muted)", fontStyle: "italic" }}>
                  Hidden — {finderName} alone knows the full detail. Find it yourself for the points.
                </span>
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}
    </div>
  );
}

function Portrait({ url, name, size }: { url: string | null; name: string; size: number }) {
  const initials = name.split(/\s+/).map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  const resolved = resolveAssetUrl(url);
  if (resolved) {
    return (
      <img
        src={resolved}
        alt={`Portrait of ${name}`}
        style={{
          width: size, height: size, objectFit: "cover", borderRadius: 6,
          flexShrink: 0, background: "#0a1018", border: "1px solid #2d3a52",
        }}
      />
    );
  }
  return (
    <div
      style={{
        width: size, height: size, borderRadius: 6, flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "linear-gradient(135deg, #2d3a52 0%, #1a2230 100%)",
        color: "var(--muted)", fontSize: size * 0.32, fontFamily: "Georgia, serif",
        border: "1px solid #2d3a52",
        position: "relative", overflow: "hidden",
      }}
      title="Portrait loading…"
    >
      <span>{initials}</span>
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0, height: 2,
        background: "var(--accent)",
        animation: "portrait-pulse 1.4s ease-in-out infinite",
        opacity: 0.6,
      }} />
    </div>
  );
}

function SidePanel({
  state, onAccuse, accusing, isOver, portraits,
  foundByPlayer, myClueText, youPlayerId, scenes,
}: any) {
  const sceneById = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of (scenes ?? [])) m.set(s.id, s.name);
    return m;
  }, [scenes]);

  return (
    <div style={styles.sidePanel}>
      <div style={styles.panelTitle}>Leaderboard &amp; finds</div>
      <ul style={styles.scoreList}>
        {[...state.players].sort((a: any, b: any) => b.points - a.points).map((p: any) => {
          const finds: ClueSummary[] = foundByPlayer[p.id] ?? [];
          const isYou = p.id === youPlayerId;
          return (
            <li key={p.id} style={styles.playerBlock}>
              <div style={styles.scoreRow}>
                <span>
                  {p.name}{state.winner_id === p.id ? " 🏆" : ""}
                  {isYou && <span style={styles.youBadge}>you</span>}
                </span>
                <b>{p.points}</b>
              </div>
              {finds.length > 0 && (
                <div style={styles.findStrip}>
                  {finds.map((c) => {
                    const fullText = isYou ? myClueText[c.clue_id]?.text ?? null : null;
                    return (
                      <ClueChip
                        key={c.clue_id}
                        summary={c}
                        fullText={fullText}
                        sceneName={c.scene_id ? sceneById.get(c.scene_id) ?? null : null}
                        finderName={p.name}
                        isYour={isYou}
                      />
                    );
                  })}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {state.mystery && !isOver && (
        <>
          <div style={{ ...styles.panelTitle, marginTop: 18 }}>Accuse</div>
          <p style={{ color: "var(--muted)", fontSize: 12, marginTop: 0 }}>
            Used {state.you?.accusations_used ?? 0}/3. Wrong = -10. Right = +50 and win.
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {state.mystery.suspects.map((s: any) => {
              const disabled = (state.you?.accusations_used ?? 0) >= 3 || accusing !== null;
              const url = portraits[s.id] ?? s.image_url ?? null;
              return (
                <button
                  key={s.id}
                  onClick={() => onAccuse(s.id, s.name)}
                  disabled={disabled}
                  style={{
                    ...(disabled ? styles.buttonDisabledSm : styles.dangerBtn),
                    display: "flex", alignItems: "center", gap: 8,
                  }}
                >
                  <Portrait url={url} name={s.name} size={32} />
                  <span>{accusing === s.id ? "Accusing…" : `Accuse ${s.name}`}</span>
                </button>
              );
            })}
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
  headerSharePlayer: {
    padding: "6px 10px", borderRadius: 4,
    background: "#2d3a52", color: "var(--ink)",
    border: "none", cursor: "pointer", fontSize: 12,
  },
  headerShareLlm: {
    padding: "6px 10px", borderRadius: 4,
    background: "rgba(212,160,78,0.18)", color: "var(--accent)",
    border: "1px solid var(--accent)", cursor: "pointer", fontSize: 12, fontWeight: 600,
  },
  grid: {
    display: "grid", gridTemplateColumns: "1.2fr 1.4fr 1fr",
    gap: 12, flex: 1, minHeight: 0,
  },
  chatPanel: {
    display: "flex", flexDirection: "column",
    background: "var(--panel)", borderRadius: 8, padding: 12, minHeight: 0,
  },
  chatHeader: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    marginBottom: 8,
  },
  turnBadgeYou: {
    background: "var(--good)", color: "#0f1520",
    fontSize: 10, fontWeight: 700, letterSpacing: 1,
    padding: "3px 8px", borderRadius: 3,
  },
  turnBadgeOther: {
    background: "rgba(138,150,168,0.18)", color: "var(--muted)",
    fontSize: 10, fontWeight: 600, letterSpacing: 0.5,
    padding: "3px 8px", borderRadius: 3,
  },
  turnArrow: {
    color: "var(--good)", fontWeight: 700, marginRight: 2,
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
  chatInputBar: {
    display: "flex", gap: 8, marginTop: 12,
    paddingTop: 8,
    borderTop: "1px solid #232c3d",
  },
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
    display: "flex", justifyContent: "space-between", padding: "4px 4px",
  },
  gameOver: {
    marginTop: 18, padding: 12, background: "rgba(212,160,78,0.10)", borderRadius: 4,
    color: "var(--accent)", textAlign: "center",
  },
  lobby: {
    background: "var(--panel)", borderRadius: 8, padding: 24, maxWidth: 600, margin: "32px auto",
  },
  narrationBlock: {
    background: "var(--panel-2)",
    border: "1px solid #2d3a52",
    borderLeft: "3px solid var(--accent)",
    borderRadius: 6,
    margin: "12px 0 16px",
    overflow: "hidden",
  },
  narrationHeader: {
    width: "100%",
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "10px 14px",
    background: "transparent",
    border: "none",
    color: "inherit",
    cursor: "pointer",
    font: "inherit",
  },
  narrationTitle: {
    fontSize: 11, textTransform: "uppercase", letterSpacing: 1.5,
    color: "var(--accent)",
  },
  narrationToggle: {
    fontSize: 11, color: "var(--muted)",
    textTransform: "uppercase", letterSpacing: 1,
  },
  narrationBody: {
    fontFamily: "Georgia, serif",
    fontSize: 13.5, lineHeight: 1.6,
    whiteSpace: "pre-wrap",
    color: "var(--ink)",
    padding: "0 14px 12px",
  },
  cursor: {
    color: "var(--accent)", animation: "cursor-blink 1s steps(2) infinite",
  },
  suspectMention: {
    color: "var(--accent)",
    fontWeight: 600,
    borderBottom: "1px dotted var(--accent)",
    cursor: "help",
  },
  accusedLine: {
    marginTop: 6,
    fontSize: 11,
    color: "var(--danger)",
  },
  accusedBadge: {
    background: "rgba(225,107,107,0.15)",
    border: "1px solid var(--danger)",
    color: "var(--danger)",
    padding: "1px 6px",
    borderRadius: 3,
    fontWeight: 600,
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  accusedNames: {
    color: "var(--muted)",
    fontSize: 11,
  },
  playerBlock: {
    padding: "8px 4px 10px",
    borderBottom: "1px solid #232c3d",
  },
  findStrip: {
    display: "flex",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 6,
  },
  clueChip: {
    width: 64,
    position: "relative",
    display: "flex", flexDirection: "column", alignItems: "center",
  },
  clueChipBtn: {
    appearance: "none",
    background: "transparent",
    border: "none",
    padding: 0,
    margin: 0,
    cursor: "pointer",
    display: "flex", flexDirection: "column", alignItems: "center",
    width: "100%",
    color: "inherit",
    font: "inherit",
  },
  clueThumb: {
    width: 56, height: 56, objectFit: "cover", borderRadius: 4,
    background: "#0a1018", border: "1px solid #2d3a52",
  },
  clueThumbFallback: {
    display: "flex", alignItems: "center", justifyContent: "center",
    color: "var(--muted)", fontWeight: 700,
  },
  clueChipLabel: {
    fontSize: 9.5,
    textAlign: "center",
    color: "var(--muted)",
    marginTop: 3,
    lineHeight: 1.15,
    width: 64,
    overflow: "hidden",
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
  },
  cluePopover: {
    // position/top/left set inline at render time (computed via getBoundingClientRect)
    width: CLUE_POP_WIDTH,
    background: "#0f1520",
    border: "1px solid var(--accent)",
    borderRadius: 6,
    boxShadow: "0 8px 28px rgba(0,0,0,0.55)",
    padding: 0,
    zIndex: 2147483000,
    display: "flex", flexDirection: "column",
    overflow: "hidden",
  },
  // Arrow on the RIGHT edge of the popover — used when popover sits to the LEFT of the chip
  cluePopArrowRight: {
    position: "absolute",
    right: -7,
    width: 12, height: 12,
    background: "#0f1520",
    borderRight: "1px solid var(--accent)",
    borderTop: "1px solid var(--accent)",
    transform: "rotate(45deg)",
  },
  // Arrow on the LEFT edge of the popover — used when popover sits to the RIGHT of the chip
  cluePopArrowLeft: {
    position: "absolute",
    left: -7,
    width: 12, height: 12,
    background: "#0f1520",
    borderLeft: "1px solid var(--accent)",
    borderBottom: "1px solid var(--accent)",
    transform: "rotate(45deg)",
  },
  cluePopImage: {
    width: "100%",
    height: 200,
    objectFit: "cover",
    display: "block",
    borderBottom: "1px solid #2d3a52",
  },
  cluePopBody: {
    padding: "10px 12px 12px",
  },
  cluePopTitle: {
    fontFamily: "Georgia, serif",
    color: "var(--accent)",
    fontSize: 15,
    fontWeight: 600,
    marginBottom: 6,
  },
  cluePopMeta: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    fontSize: 11,
    color: "var(--muted)",
    marginBottom: 4,
  },
  cluePopPoints: {
    color: "var(--good)",
    fontWeight: 700,
  },
  cluePopScene: {
    fontSize: 11,
    color: "var(--muted)",
    marginBottom: 8,
  },
  cluePopText: {
    fontSize: 12.5,
    lineHeight: 1.5,
    color: "var(--ink)",
    borderTop: "1px solid #232c3d",
    paddingTop: 8,
  },
  youBadge: {
    marginLeft: 6, fontSize: 9, textTransform: "uppercase",
    background: "var(--accent)", color: "#1a1410",
    padding: "1px 5px", borderRadius: 3, letterSpacing: 1,
    verticalAlign: "middle",
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
  sharePanel: {
    margin: "16px 0 24px",
    padding: 14,
    background: "var(--panel-2)",
    borderRadius: 6,
    border: "1px solid #2d3a52",
  },
  shareTitle: {
    fontSize: 12, textTransform: "uppercase", letterSpacing: 1.5,
    color: "var(--accent)", marginBottom: 10,
  },
  shareLabel: {
    fontSize: 11, color: "var(--muted)",
    textTransform: "uppercase", letterSpacing: 1, marginBottom: 4,
  },
  copyRow: { display: "flex", gap: 6 },
  copyInput: {
    flex: 1, padding: "7px 10px",
    background: "#0a1018", border: "1px solid #2d3a52",
    color: "var(--ink)", borderRadius: 4,
    fontFamily: "ui-monospace, monospace", fontSize: 12,
  },
  copyBtn: {
    padding: "7px 14px", borderRadius: 4,
    background: "#2d3a52", color: "var(--ink)",
    border: "none", cursor: "pointer", fontSize: 12, fontWeight: 600,
    minWidth: 78,
  },
  shareHint: {
    marginTop: 6, color: "var(--muted)", fontSize: 11, lineHeight: 1.45,
  },
};
