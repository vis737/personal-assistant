import { useCallback, useEffect, useRef, useState } from "react";

import { fetchState, sendToJarvis, type JarvisState } from "@/lib/jarvis";

const POLL_MS = 800;

/** Live engine state + send(), with a connection flag for the HUD badge. */
export function useJarvis() {
  const [state, setState] = useState<JarvisState | null>(null);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const next = await fetchState();
        if (!alive) return;
        setState(next);
        setConnected(true);
        setError(null);
      } catch (err) {
        if (!alive) return;
        setConnected(false);
        setError(err instanceof Error ? err.message : "engine unreachable");
      }
    };
    void tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await sendToJarvis(trimmed);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "send failed");
    } finally {
      setBusy(false);
    }
  }, []);

  return { state, connected, busy, error, send };
}

type SpeechRecognitionLike = {
  lang: string;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }> > }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
};

function getRecognitionCtor():
  | (new () => SpeechRecognitionLike)
  | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  return (w["SpeechRecognition"] ?? w["webkitSpeechRecognition"]) as
    | (new () => SpeechRecognitionLike)
    | null;
}

/**
 * Push-to-talk using the browser's built-in Web Speech API (free, no key).
 * onText receives each final transcript, which the caller sends to the engine.
 */
export function useDictation(onText: (text: string) => void) {
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [denied, setDenied] = useState(false);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const onTextRef = useRef(onText);

  useEffect(() => {
    onTextRef.current = onText;
  }, [onText]);

  useEffect(() => {
    setSupported(Boolean(getRecognitionCtor()));
    return () => {
      try {
        recRef.current?.stop();
      } catch {
        /* ignore */
      }
    };
  }, []);

  const stop = useCallback(() => {
    try {
      recRef.current?.stop();
    } catch {
      /* ignore */
    }
    recRef.current = null;
    setListening(false);
  }, []);

  const toggle = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return;
    if (recRef.current) {
      stop();
      return;
    }
    try {
      const rec = new Ctor();
      rec.lang = "en-US";
      rec.interimResults = false;
      rec.maxAlternatives = 1;
      rec.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript;
        if (transcript) onTextRef.current(transcript);
      };
      rec.onerror = () => {
        setDenied(true);
        setListening(false);
        recRef.current = null;
      };
      rec.onend = () => {
        setListening(false);
        recRef.current = null;
      };
      rec.start();
      recRef.current = rec;
      setListening(true);
      setDenied(false);
    } catch {
      setListening(false);
    }
  }, [stop]);

  return { supported, listening, denied, toggle };
}
