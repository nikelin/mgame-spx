import { useState } from "react";
import { api, type Language } from "../api";
import type { Session } from "../App";

interface Props {
  onJoined: (s: Session) => void;
  initialCode?: string | null;
}

export function Home({ onJoined, initialCode }: Props) {
  // If the user landed via a shareable URL (?room=ABCD), force the Join tab and pre-fill
  // the code so they only need to enter their name.
  const [mode, setMode] = useState<"create" | "join">(initialCode ? "join" : "create");
  const [name, setName] = useState("");
  const [code, setCode] = useState(initialCode ?? "");
  const [apiKey, setApiKey] = useState("");
  const [language, setLanguage] = useState<Language>("en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode === "create") {
        const r = await api.createRoom(name.trim(), apiKey.trim() || null, language);
        onJoined({ code: r.code, playerId: r.player_id, token: r.token, isHost: r.is_host, name: name.trim() });
      } else {
        const r = await api.joinRoom(code.trim().toUpperCase(), name.trim());
        onJoined({ code: r.code, playerId: r.player_id, token: r.token, isHost: r.is_host, name: name.trim() });
      }
    } catch (e: any) {
      // Surface a clearer message when the game has already started — the server returns
      // a 409 with the explanation, so we mostly just pass it through.
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = name.trim() && (mode === "create" || code.trim().length >= 3);

  return (
    <div style={styles.shell}>
      <div style={styles.card}>
        <h1 style={styles.title}>spgame</h1>
        <p style={styles.subtitle}>
          Where does the SF startup's product-market fit live? A collaborative investigation.
          Procedurally generated. Figure out whose hypothesis is right before anyone else does.
        </p>

        <div style={styles.tabs}>
          <button
            style={mode === "create" ? styles.tabActive : styles.tab}
            onClick={() => setMode("create")}
          >Create a room</button>
          <button
            style={mode === "join" ? styles.tabActive : styles.tab}
            onClick={() => setMode("join")}
          >Join a room</button>
        </div>

        {initialCode && (
          <div style={styles.invite}>
            You were invited to room <code style={styles.inviteCode}>{initialCode}</code> — enter your name to join.
          </div>
        )}

        <label style={styles.label}>Your name</label>
        <input
          style={styles.input}
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Sherlock"
          maxLength={40}
          autoFocus
        />

        {mode === "join" && (
          <>
            <label style={styles.label}>Room code</label>
            <input
              style={{ ...styles.input, textTransform: "uppercase", letterSpacing: 4, fontSize: 18 }}
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))}
              placeholder="ABCD"
              maxLength={4}
            />
          </>
        )}

        {mode === "create" && (
          <>
            <label style={styles.label}>Language</label>
            <div style={styles.flagRow}>
              <button
                type="button"
                onClick={() => setLanguage("en")}
                style={language === "en" ? styles.flagBtnActive : styles.flagBtn}
                aria-pressed={language === "en"}
                aria-label="English"
              >
                <span style={styles.flagEmoji} aria-hidden="true">🇬🇧</span>
                <span>English</span>
              </button>
              <button
                type="button"
                onClick={() => setLanguage("pt")}
                style={language === "pt" ? styles.flagBtnActive : styles.flagBtn}
                aria-pressed={language === "pt"}
                aria-label="Portuguese"
              >
                <span style={styles.flagEmoji} aria-hidden="true">🇵🇹</span>
                <span>Português</span>
              </button>
            </div>

            <label style={styles.label}>OpenAI API key (optional)</label>
            <input
              style={{ ...styles.input, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-... (leave blank to use the server's shared key)"
              autoComplete="off"
              spellCheck={false}
            />
            <div style={styles.hint}>
              If you provide your own key, every LLM call for THIS room (mystery, narration,
              storyteller turns, clue matching) will use it instead of the server's default.
              The key is stored only on the room's server-side state.
            </div>
          </>
        )}

        {error && <div style={styles.error}>{error}</div>}

        <button
          style={canSubmit && !busy ? styles.button : styles.buttonDisabled}
          disabled={!canSubmit || busy}
          onClick={submit}
        >
          {busy ? "Working…" : mode === "create" ? "Create room" : "Join room"}
        </button>

        <p style={styles.muted}>
          Want to play from a Claude/ChatGPT session instead? Paste your room URL +{" "}
          <code>/llm</code> into the chat — the model will read the instructions and play
          alongside humans.
        </p>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: "100vh",
    display: "flex", alignItems: "center", justifyContent: "center",
    background: "radial-gradient(circle at 30% 20%, #1c2436 0%, #0e131b 60%)",
  },
  card: {
    background: "var(--panel)", padding: 32, borderRadius: 12,
    width: 420, maxWidth: "90vw",
    boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
  },
  title: { margin: 0, fontSize: 36, fontFamily: "Georgia, serif", color: "var(--accent)" },
  subtitle: { color: "var(--muted)", marginTop: 4, marginBottom: 24 },
  tabs: { display: "flex", gap: 8, marginBottom: 20 },
  tab: {
    flex: 1, padding: "8px 12px", border: "1px solid #2d3a52", background: "transparent",
    color: "var(--ink)", borderRadius: 6, cursor: "pointer",
  },
  tabActive: {
    flex: 1, padding: "8px 12px", border: "1px solid var(--accent)",
    background: "rgba(212,160,78,0.15)", color: "var(--accent)",
    borderRadius: 6, cursor: "pointer", fontWeight: 600,
  },
  label: { display: "block", marginTop: 12, marginBottom: 6, color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: 1 },
  input: {
    width: "100%", padding: "10px 12px", borderRadius: 6, border: "1px solid #2d3a52",
    background: "#0f1520", color: "var(--ink)", fontSize: 14,
  },
  error: { marginTop: 12, color: "var(--danger)", fontSize: 13 },
  button: {
    marginTop: 20, width: "100%", padding: "12px 16px", borderRadius: 6,
    background: "var(--accent)", color: "#1a1410", fontWeight: 600,
    border: "none", cursor: "pointer", fontSize: 15,
  },
  buttonDisabled: {
    marginTop: 20, width: "100%", padding: "12px 16px", borderRadius: 6,
    background: "#3a4256", color: "#7a8298", fontWeight: 600,
    border: "none", cursor: "not-allowed", fontSize: 15,
  },
  muted: { color: "var(--muted)", marginTop: 24, fontSize: 12, lineHeight: 1.5 },
  hint: { color: "var(--muted)", marginTop: 6, fontSize: 11, lineHeight: 1.45 },
  flagRow: { display: "flex", gap: 8 },
  flagBtn: {
    flex: 1,
    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
    padding: "10px 12px", borderRadius: 6,
    border: "1px solid #2d3a52", background: "transparent",
    color: "var(--muted)", cursor: "pointer",
    fontSize: 13,
  },
  flagBtnActive: {
    flex: 1,
    display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
    padding: "10px 12px", borderRadius: 6,
    border: "1px solid var(--accent)",
    background: "rgba(212,160,78,0.15)",
    color: "var(--accent)", cursor: "pointer",
    fontSize: 13, fontWeight: 600,
  },
  flagEmoji: { fontSize: 22, lineHeight: 1 },
  invite: {
    background: "rgba(212,160,78,0.10)",
    border: "1px solid var(--accent)",
    borderRadius: 4,
    padding: "8px 12px",
    color: "var(--ink)",
    fontSize: 12,
    marginBottom: 12,
  },
  inviteCode: {
    color: "var(--accent)",
    fontWeight: 700,
    letterSpacing: 3,
    background: "#0a1018",
    padding: "1px 6px",
    borderRadius: 3,
  },
};
