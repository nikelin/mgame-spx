// Lightweight i18n. STRINGS[key][lang] is the translated string; `{placeholder}` tokens
// are substituted from the optional params object. Falls back to English if a translation
// is missing.
//
// Keep this file in sync with the language Literal on the server's GameRoom model.

import type { Language } from "./api";

type Translations = { en: string; pt: string };
type StringMap = Record<string, Translations>;

export const STRINGS: StringMap = {
  // ===== Home =====
  "home.subtitle": {
    en: "A collaborative whodunit. Procedurally generated. Solve it first.",
    pt: "Um mistério colaborativo. Gerado proceduralmente. Resolva-o primeiro.",
  },
  "home.tab.create": { en: "Create a room", pt: "Criar uma sala" },
  "home.tab.join":   { en: "Join a room",   pt: "Entrar numa sala" },
  "home.invite": {
    en: "You were invited to room {code} — enter your name to join.",
    pt: "Foi convidado para a sala {code} — introduza o seu nome para entrar.",
  },
  "home.label.name":   { en: "Your name",   pt: "O seu nome" },
  "home.placeholder.name": { en: "e.g. Sherlock", pt: "ex. Sherlock" },
  "home.label.code":   { en: "Room code",   pt: "Código da sala" },
  "home.label.language": { en: "Language",  pt: "Idioma" },
  "home.label.api_key": {
    en: "OpenAI API key (optional)",
    pt: "Chave da API OpenAI (opcional)",
  },
  "home.placeholder.api_key": {
    en: "sk-... (leave blank to use the server's shared key)",
    pt: "sk-... (deixe em branco para usar a chave partilhada do servidor)",
  },
  "home.hint.api_key": {
    en: "If you provide your own key, every LLM call for THIS room (mystery, narration, storyteller turns, clue matching) will use it instead of the server's default. The key is stored only on the room's server-side state.",
    pt: "Se fornecer a sua própria chave, todas as chamadas ao LLM desta sala (mistério, narração, turnos do narrador, correspondência de pistas) usarão a sua chave em vez da chave padrão do servidor. A chave é guardada apenas no estado da sala no servidor.",
  },
  "home.button.create": { en: "Create room", pt: "Criar sala" },
  "home.button.join":   { en: "Join room",   pt: "Entrar na sala" },
  "home.button.busy":   { en: "Working…",     pt: "A processar…" },
  "home.footer": {
    en: "Want to play from a Claude/ChatGPT session instead? Paste your room URL + /llm into the chat — the model will read the instructions and play alongside humans.",
    pt: "Quer jogar a partir de uma sessão do Claude/ChatGPT? Cole o URL da sala + /llm na conversa — o modelo lerá as instruções e jogará junto dos humanos.",
  },

  // ===== Game header =====
  "game.room":   { en: "Room",    pt: "Sala" },
  "game.status": { en: "Status:", pt: "Estado:" },
  "game.status.lobby":   { en: "lobby",      pt: "à espera" },
  "game.status.playing": { en: "playing",    pt: "em jogo" },
  "game.status.over":    { en: "over",       pt: "terminado" },
  "game.copy.player_link": { en: "Copy Player link", pt: "Copiar link do jogador" },
  "game.copy.llm_link":    { en: "Copy LLM link",    pt: "Copiar link LLM" },
  "game.copied":           { en: "Copied ✓",         pt: "Copiado ✓" },
  "game.leave":            { en: "Leave",            pt: "Sair" },

  // ===== Lobby =====
  "lobby.title":     { en: "Waiting to start", pt: "À espera de começar" },
  "lobby.subtitle": {
    en: "Share the room code {code} with other players.",
    pt: "Partilhe o código {code} com os outros jogadores.",
  },
  "lobby.players":   { en: "Players in the room", pt: "Jogadores na sala" },
  "lobby.button.start":      { en: "Start the game",        pt: "Começar o jogo" },
  "lobby.button.starting":   { en: "Generating mystery…",   pt: "A gerar o mistério…" },
  "lobby.waiting_for_host":  { en: "Waiting for the host to start the game…", pt: "À espera que o anfitrião comece o jogo…" },

  // ===== Share panel =====
  "share.title":          { en: "Invite others", pt: "Convide outros" },
  "share.label.player":   { en: "Player link — for humans", pt: "Link do jogador — para humanos" },
  "share.label.llm":      { en: "LLM link — paste into Claude / ChatGPT", pt: "Link LLM — cole no Claude / ChatGPT" },
  "share.button.copy":    { en: "Copy", pt: "Copiar" },
  "share.hint": {
    en: "The LLM URL returns a Markdown briefing with the room context and curl examples; replace YOUR_BOT_NAME with whatever you'd like the LLM-controlled player to be called.",
    pt: "O URL LLM devolve um briefing em Markdown com o contexto da sala e exemplos de curl; substitua YOUR_BOT_NAME pelo nome que quiser dar ao jogador controlado pelo LLM.",
  },

  // ===== Mystery panel =====
  "mystery.panel":       { en: "The case",          pt: "O caso" },
  "mystery.victim":      { en: "Victim:",           pt: "Vítima:" },
  "mystery.narration":   { en: "Opening narration", pt: "Narração inicial" },
  "mystery.show":        { en: "▸ show",            pt: "▸ mostrar" },
  "mystery.hide":        { en: "▾ hide",            pt: "▾ ocultar" },
  "mystery.throat":      { en: "The storyteller clears their throat…", pt: "O narrador limpa a garganta…" },
  "mystery.suspects":    { en: "Suspects", pt: "Suspeitos" },
  "mystery.scenes":      { en: "Scenes",   pt: "Cenas" },
  "mystery.alibi":       { en: "Alibi:",   pt: "Álibi:" },
  "mystery.clue_count": {
    en: "{total} total clues. You've found {found}.",
    pt: "{total} pistas no total. Encontrou {found}.",
  },
  "mystery.accused": {
    en: "Accused {count}× {plural}",
    pt: "Acusado {count}× {plural}",
  },
  "mystery.accused.times":    { en: "times", pt: "vezes" },
  "mystery.accused.by":       { en: "by",    pt: "por" },

  // ===== Sidebar =====
  "side.title":            { en: "Leaderboard & finds", pt: "Classificação e pistas" },
  "side.you_badge":        { en: "you",                  pt: "tu" },
  "side.accuse":           { en: "Accuse",               pt: "Acusar" },
  "side.accuse.hint": {
    en: "Used {used}/3. Wrong = -10. Right = +50 and win.",
    pt: "Usadas {used}/3. Errada = -10. Certa = +50 e ganha.",
  },
  "side.accuse.button":   { en: "Accuse {name}",  pt: "Acusar {name}" },
  "side.accuse.busy":     { en: "Accusing…",       pt: "A acusar…" },
  "side.game_over":       { en: "Game over. Refresh to start a new room.", pt: "Jogo terminado. Atualize para iniciar uma nova sala." },

  // ===== Chat =====
  "chat.title":            { en: "Storyteller", pt: "Narrador" },
  "chat.placeholder":      { en: "Ask the storyteller… (specific = better)", pt: "Pergunte ao narrador… (específico = melhor)" },
  "chat.placeholder.over": { en: "Game over.", pt: "Jogo terminado." },
  "chat.send":             { en: "Send", pt: "Enviar" },

  // ===== Chat lines =====
  "chat.line.story":       { en: "Storyteller (to {who}):", pt: "Narrador (para {who}):" },
  "chat.line.story.noname": { en: "Storyteller:", pt: "Narrador:" },
  "chat.line.clue_header": {
    en: "You uncovered {n} clue(s): +{points} pts",
    pt: "Descobriu {n} pista(s): +{points} pts",
  },
  "chat.line.wrong_accuse": {
    en: "{name} wrongly accused {suspect} (-{penalty} pts).",
    pt: "{name} acusou erradamente {suspect} (-{penalty} pts).",
  },
  "chat.line.win": {
    en: "🏆 {name} correctly accused {suspect}! Motive: {motive}",
    pt: "🏆 {name} acusou corretamente {suspect}! Motivo: {motive}",
  },
  "chat.line.joined": {
    en: "{name} joined the room.",
    pt: "{name} entrou na sala.",
  },
  "chat.line.start": {
    en: "The mystery \"{title}\" has begun.",
    pt: "O mistério «{title}» começou.",
  },
  "chat.line.clue_summary": {
    en: "{name} uncovered {n} clue(s) (+{points} pts).",
    pt: "{name} descobriu {n} pista(s) (+{points} pts).",
  },

  // ===== Clue tooltip =====
  "tooltip.unknown":   { en: "Unknown evidence", pt: "Evidência desconhecida" },
  "tooltip.found_by":  { en: "Found by", pt: "Encontrada por" },
  "tooltip.scene":     { en: "Scene:", pt: "Cena:" },
  "tooltip.points":    { en: "+{n} pts", pt: "+{n} pts" },
  "tooltip.hidden": {
    en: "Hidden — {name} alone knows the full detail. Find it yourself for the points.",
    pt: "Oculta — apenas {name} conhece os detalhes completos. Encontre-a para ganhar os pontos.",
  },
  "tooltip.missing": {
    en: "Full description not loaded — try reopening the room.",
    pt: "Descrição completa não carregada — tente reabrir a sala.",
  },
  "tooltip.inspect":  { en: "Inspect {title}", pt: "Inspecionar {title}" },

  // ===== Errors / misc =====
  "error.prefix":     { en: "Error:", pt: "Erro:" },
  "loading.room":     { en: "Loading room {code}…", pt: "A carregar a sala {code}…" },
};

export type StringKey = string;

/**
 * Translate a key into the given language, substituting any `{name}` placeholders
 * from the params object. Falls back to English (or the key itself) when missing.
 */
export function t(key: StringKey, lang: Language, params?: Record<string, string | number>): string {
  const entry = STRINGS[key];
  if (!entry) return key;
  let s = entry[lang] ?? entry.en ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      s = s.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return s;
}
