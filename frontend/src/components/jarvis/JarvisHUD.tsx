import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useDictation, useJarvis } from "@/hooks/useJarvis";
import { tickerLabel, type Msg } from "@/lib/jarvis";

import { Core3D } from "./Core3D";
import universe from "@/assets/universe.jpg";

function useClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => { setNow(new Date()); const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  return now;
}

function Panel({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`hud-panel p-3 flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <h2 className="hud-title">{title}</h2>
        <span className="h-1.5 w-1.5 rounded-full bg-gold animate-flicker" />
      </div>
      <div className="flex-1 min-h-0">{children}</div>
    </section>
  );
}

function Oscilloscope({ level }: { level: React.MutableRefObject<number> }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current!; const ctx = c.getContext("2d")!; let raf = 0; let t = 0;
    const color = getComputedStyle(document.documentElement).getPropertyValue("--primary").trim();
    const draw = () => {
      t += 0.06; const w = (c.width = c.clientWidth * 2), h = (c.height = c.clientHeight * 2);
      ctx.clearRect(0, 0, w, h); ctx.lineWidth = 3; ctx.strokeStyle = `oklch(${color.replace(/oklch\(|\)/g, "")})`;
      ctx.shadowBlur = 12; ctx.shadowColor = ctx.strokeStyle; ctx.beginPath();
      const amp = 6 + level.current * h * 0.4;
      for (let x = 0; x < w; x += 4) {
        const y = h / 2 + Math.sin(x * 0.02 + t) * amp * Math.sin(x * 0.004 + t * 0.3) + Math.sin(x * 0.09 - t * 2) * amp * 0.25;
        x ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.stroke(); level.current *= 0.96; raf = requestAnimationFrame(draw);
    };
    draw(); return () => cancelAnimationFrame(raf);
  }, [level]);
  return <canvas ref={ref} className="w-full h-16" />;
}

const STAGES = ["MIC", "EAR", "MIND", "HANDS", "VOICE"];
const SUB = ["stream", "recognize", "reason", "execute", "respond"];
const FALLBACK_MARKETS = [
  { s: "STRK", n: "Stark Industries", p: 412.88, c: 2.41 },
  { s: "NVDA", n: "Nvidia", p: 188.2, c: 1.12 },
  { s: "BTC", n: "Bitcoin", p: 98420, c: -0.84 },
  { s: "AAPL", n: "Apple", p: 241.6, c: 0.37 },
  { s: "OSCP", n: "Oscorp", p: 61.04, c: -3.2 },
];
const GREETING: Msg = { who: "jarvis", text: "Good afternoon, sir. All systems are online." };

export function JarvisHUD() {
  const now = useClock();
  const level = useRef(0);
  const [stage, setStage] = useState(-1);
  const [input, setInput] = useState("");
  const [fallbackVitals, setFallbackVitals] = useState({ cpu: 34, mem: 62, disk: 71, battery: 98 });

  const { state, connected, busy, error, send } = useJarvis();
  const logRef = useRef<HTMLDivElement>(null);

  const status = state?.status ?? (connected ? "LISTENING" : "OFFLINE");
  // Conversation window opened by the wake word: mic accepts follow-ups.
  const followupLeft = Math.max(0, Math.ceil((state?.followup_until ?? 0) - (now?.getTime() ?? 0) / 1000));
  // Visible acknowledgement for a few seconds after the wake word is heard.
  const wokeFor = state?.last_wake_at
    ? Math.max(0, 3.5 - (now?.getTime() ?? 0) / 1000 + state.last_wake_at)
    : 0;
  const messages: Msg[] = state?.messages?.length ? state.messages : [GREETING];

  // Mirror the engine's real microphone energy into the scope.
  useEffect(() => {
    const level_now = state?.level ?? 0;
    if (level_now > 0.02) level.current = Math.max(level.current, level_now);
  }, [state?.level]);

  // Drive the pipeline display from the engine's actual state machine.
  useEffect(() => {
    if (status === "PROCESSING") {
      let i = 0;
      setStage(0);
      const id = window.setInterval(() => { i = Math.min(i + 1, 3); setStage(i); }, 350);
      return () => window.clearInterval(id);
    }
    if (status === "SPEAKING") { setStage(4); return; }
    setStage(-1);
    return undefined;
  }, [status]);

  // Fallback animation only when no real vitals are coming in.
  useEffect(() => {
    if (state?.vitals) return undefined;
    const t = setInterval(() => setFallbackVitals((v) => ({
      cpu: Math.max(5, Math.min(99, v.cpu + (Math.random() - 0.5) * 10)),
      mem: Math.max(20, Math.min(95, v.mem + (Math.random() - 0.5) * 4)),
      disk: v.disk,
      battery: Math.max(90, Math.min(100, v.battery + (Math.random() - 0.5) * 1)),
    })), 1500);
    return () => clearInterval(t);
  }, [state?.vitals]);

  useEffect(() => { logRef.current?.scrollTo({ top: 1e6, behavior: "smooth" }); }, [messages.length]);

  const runQuery = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    await send(trimmed);
  }, [busy, send]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = input;
    setInput("");
    void runQuery(text);
  };

  const dictation = useDictation(useCallback((text: string) => { void runQuery(text); }, [runQuery]));

  const vitals = state?.vitals;
  const vitalsRows: [string, number][] = vitals
    ? [
        ["cpu", vitals.cpu],
        ["mem", vitals.mem],
        ["disk", vitals.disk],
        ["battery", vitals.battery ?? 100],
      ]
    : [
        ["cpu", fallbackVitals.cpu],
        ["mem", fallbackVitals.mem],
        ["disk", fallbackVitals.disk],
        ["battery", fallbackVitals.battery],
      ];

  const marketItems = state?.market?.items?.length
    ? state.market.items.slice(0, 6).map((m) => ({
        s: tickerLabel(m.symbol),
        n: m.name,
        p: m.price,
        c: m.pct_change,
        signal: m.signal,
      }))
    : FALLBACK_MARKETS;

  const briefings = useMemo(() => {
    const out: { t: string; m: string }[] = [];
    const w = state?.weather;
    if (w?.ok) {
      out.push({ t: "WX", m: `${w.place ?? "Location"}: ${w.temp}°C ${w.text}, feels like ${w.feels}°C` });
      const tomorrow = w.days?.[1];
      if (tomorrow) out.push({ t: "+1", m: `Tomorrow: ${tomorrow.text}, ${tomorrow.max}/${tomorrow.min}°C` });
    } else {
      out.push({ t: "WX", m: "Weather engine warming up…" });
    }
    const mail = state?.mail;
    if (mail?.configured && mail.ok) out.push({ t: "INBOX", m: mail.text ?? "" });
    else out.push({ t: "INBOX", m: "Mail not configured — add ~/.openjarvis/mail.ini" });
    out.push({ t: "LLM", m: state?.brain?.ready ? "Groq cloud brain online" : "Offline fallback (no API key)" });
    return out;
  }, [state?.weather, state?.mail, state?.brain?.ready]);

  const pad = (n: number) => String(n).padStart(2, "0");
  const time = now ? `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}` : "--:--:--";
  const date = now ? now.toLocaleDateString("en-GB", { weekday: "long", day: "2-digit", month: "long", year: "numeric" }).toUpperCase() : "";
  const micActive = status === "LISTENING" || status === "SPEAKING";

  return (
    <main className="jarvis-screen relative h-screen w-screen overflow-hidden">
      <img src={universe} alt="" width={1920} height={1088} className="absolute inset-0 h-full w-full object-cover" />
      <div className="jarvis-scene absolute inset-0"><Core3D level={level} /></div>
      <div className="absolute inset-0 scanlines" />

      <div className="relative z-10 grid h-full grid-rows-[auto_1fr_auto] gap-3 p-4 pointer-events-none">
        <header className="jarvis-header flex items-start justify-between">
          <div>
            <p className="text-[10px] text-muted-foreground">STARK INDUSTRIES // SECURE UPLINK</p>
            <h1 className="font-display text-3xl tracking-[0.5em] text-primary glow-text">J.A.R.V.I.S</h1>
            <p className="text-[10px] glow-gold">MARK VII EXECUTIVE ASSISTANT · ACTIVE</p>
          </div>
          <div className="text-center">
            <div className="font-display text-4xl tracking-widest text-primary glow-text">{time}</div>
            <p className="text-[10px] text-muted-foreground tracking-[0.3em]">{date}</p>
          </div>
          <div className="text-right text-[10px] text-muted-foreground space-y-1">
            <p>
              BRAIN: <span className={state?.brain?.ready ? "text-success" : "text-destructive"}>
                {state?.brain?.ready ? "GROQ" : "OFFLINE"}
              </span>{" "}
              · ARC: <span className="glow-gold">{(vitalsRows[3]?.[1] ?? 0).toFixed(1)}%</span>
            </p>
            <p className={connected ? "text-success" : "text-destructive animate-flicker"}>
              ● {connected ? "ENGINE LINKED" : "ENGINE OFFLINE"}
            </p>
          </div>
        </header>

        <div className="jarvis-columns grid min-h-0 grid-cols-[minmax(260px,22vw)_1fr_minmax(260px,22vw)] gap-4">
          <div className="flex min-h-0 flex-col gap-3 pointer-events-auto">
            <Panel title="VOICE FREQUENCY">
              <Oscilloscope level={level} />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>
                  {status}
                  {followupLeft > 0 ? (
                    <span className="text-gold"> · OPEN {followupLeft}s</span>
                  ) : null}
                </span>
                <span className={dictation.listening ? "text-gold" : "text-success"}>
                  {dictation.listening ? "BROWSER MIC" : "MIC READY"}
                </span>
              </div>
            </Panel>
            <Panel title="NEURAL CORE PIPELINE">
              <div className="grid grid-cols-5 gap-1">
                {STAGES.map((s, i) => (
                  <div key={s} className={`border p-1 text-center transition-all ${stage === i ? "border-gold bg-gold/15 shadow-[var(--glow-gold)]" : stage > i ? "border-primary bg-primary/10" : ""}`}>
                    <div className="text-[10px] text-primary">{s}</div>
                    <div className="text-[8px] text-muted-foreground">{SUB[i]}</div>
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="TELEMETRY LOGS & CHAT" className="flex-1">
              <div className="flex h-full flex-col">
                <div ref={logRef} className="flex-1 min-h-0 overflow-y-auto space-y-2 pr-1 text-xs">
                  {messages.map((m, i) => (
                    <p key={`${m.t ?? i}-${i}`}>
                      <span className={m.who === "jarvis" ? "glow-gold" : "text-primary"}>
                        {m.who === "jarvis" ? "JARVIS ▸ " : "YOU ▸ "}
                      </span>
                      <span className="text-foreground/90">{m.text}</span>
                    </p>
                  ))}
                  {error ? <p className="text-destructive text-[10px]">ENGINE ▸ {error}</p> : null}
                </div>
                <form onSubmit={onSubmit} className="mt-2 flex gap-2">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    placeholder="Speak, sir…"
                    className="flex-1 bg-transparent border border-input px-2 py-1 text-xs outline-none focus:border-primary focus:shadow-[var(--glow)] placeholder:text-muted-foreground"
                  />
                  {dictation.supported ? (
                    <button
                      type="button"
                      onClick={dictation.toggle}
                      aria-pressed={dictation.listening}
                      className={`border px-3 text-[10px] font-display tracking-widest ${dictation.listening ? "border-destructive text-destructive" : "border-primary text-primary hover:bg-primary/15"}`}
                    >
                      {dictation.listening ? "STOP" : "MIC"}
                    </button>
                  ) : null}
                  <button
                    type="submit"
                    disabled={busy}
                    className="border border-gold px-3 text-[10px] font-display tracking-widest text-gold hover:bg-gold/15 disabled:opacity-50"
                  >
                    {busy ? "…" : "SEND"}
                  </button>
                </form>
              </div>
            </Panel>
          </div>

          <div className="jarvis-center flex flex-col items-center justify-end pb-6">
            <div className={`font-display text-4xl tracking-[0.6em] glow-text ${wokeFor > 0 ? "text-gold" : "text-primary"}`}>
              {wokeFor > 0 ? "LISTENING" : status}
            </div>
            <div className="mt-1 text-[10px] tracking-[0.4em] text-muted-foreground">
              {wokeFor > 0
                ? `WAKE WORD DETECTED${followupLeft > 0 ? ` · OPEN ${followupLeft}s` : ""}`
                : "SYSTEM OPERATIONAL"}
            </div>
          </div>

          <div className="flex min-h-0 flex-col gap-3 pointer-events-auto">
            <Panel title="LIVE MARKET INTELLIGENCE">
              <ul className="space-y-1.5 text-xs">
                {marketItems.map((m) => (
                  <li key={m.s} className="flex justify-between border-b border-border/50 pb-1">
                    <span>
                      <span className="text-primary">{m.s}</span>{" "}
                      <span className="text-muted-foreground text-[10px]">{m.n}</span>
                    </span>
                    <span>
                      {m.p.toLocaleString("en-IN")}{" "}
                      <span className={m.c >= 0 ? "text-success" : "text-destructive"}>
                        {m.c >= 0 ? "▲" : "▼"}{Math.abs(m.c).toFixed(2)}%
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
            <Panel title="EXECUTIVE BRIEFINGS & INBOX" className="flex-1">
              <ul className="space-y-2 text-xs overflow-y-auto h-full">
                {briefings.map((b) => (
                  <li key={b.t} className="border-l-2 border-gold pl-2">
                    <span className="glow-gold">{b.t}</span> <span className="text-foreground/90">{b.m}</span>
                  </li>
                ))}
              </ul>
            </Panel>
            <Panel title="HARDWARE VITALS">
              {vitalsRows.map(([k, v]) => (
                <div key={k} className="mb-1.5">
                  <div className="flex justify-between text-[10px]">
                    <span className="text-muted-foreground uppercase">{k}</span>
                    <span className="text-primary">{v.toFixed(0)}%</span>
                  </div>
                  <div className="h-1 bg-muted"><div className="h-full bar-fill transition-all duration-700" style={{ width: `${v}%` }} /></div>
                </div>
              ))}
            </Panel>
          </div>
        </div>

        <footer className="flex justify-between text-[10px] text-muted-foreground">
          <span>FAILOVER: {state?.brain?.ready ? "HYBRID LOCAL/CLOUD" : "LOCAL FALLBACK"}</span>
          <span className="glow-gold">{micActive ? "● MICROPHONE ACTIVE" : "\"SOMETIMES YOU GOTTA RUN BEFORE YOU CAN WALK.\""}</span>
          <span>ENCRYPTION: 4096-BIT RSA</span>
        </footer>
      </div>
    </main>
  );
}
