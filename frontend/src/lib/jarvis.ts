/** Typed client for the local Jarvis engine API (127.0.0.1:8787). */

export const API_BASE = "http://127.0.0.1:8787";

export type Msg = { who: "you" | "jarvis"; text: string; t?: number };

export type MarketItem = {
  symbol: string;
  name: string;
  price: number;
  change?: number;
  pct_change: number;
  currency: string;
  signal?: string;
  reason?: string;
};

export type WeatherDay = { date: string; text: string; max: number | null; min: number | null };

export type Weather = {
  ok: boolean;
  place?: string;
  temp?: number;
  feels?: number;
  humidity?: number;
  wind?: number;
  text?: string;
  days?: WeatherDay[];
  error?: string;
};

export type Mail = {
  ok: boolean;
  configured?: boolean;
  count?: number;
  items?: { from: string; subject: string; date?: string }[];
  text?: string;
};

export type Vitals = {
  cpu: number;
  mem: number;
  disk: number;
  uptime: number;
  battery: number | null;
};

export type JarvisState = {
  status: string;
  level: number;
  /** Conversation window end (epoch seconds): until then the mic accepts
   * commands without the wake word. 0 = wake word required. */
  followup_until?: number;
  /** Last time the wake word was heard (epoch seconds) - drives the
   * LISTENING acknowledgement. */
  last_wake_at?: number;
  messages: Msg[];
  market: { items: MarketItem[] } | null;
  weather: Weather | null;
  mail: Mail | null;
  vitals: Vitals | null;
  brain: { ready: boolean; cooldown_until: number };
  started_at: number;
  seq: number;
};

export async function fetchState(): Promise<JarvisState> {
  const res = await fetch(`${API_BASE}/api/state`, { cache: "no-store" });
  if (!res.ok) throw new Error(`state ${res.status}`);
  return (await res.json()) as JarvisState;
}

export async function sendToJarvis(text: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  const payload = (await res.json().catch(() => null)) as { ok?: boolean; reply?: string; error?: string } | null;
  if (!res.ok || !payload?.ok) throw new Error(payload?.error ?? `chat ${res.status}`);
  return payload.reply ?? "";
}

/** Short, readable ticker label: "^NSEI" -> "NSEI", "RELIANCE.NS" -> "RELIANCE". */
export function tickerLabel(symbol: string): string {
  return symbol.replace(/^\^/, "").replace(/\.(NS|BO)$/i, "");
}
