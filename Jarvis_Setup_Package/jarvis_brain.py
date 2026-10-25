"""
jarvis_brain.py - Hybrid Cloud + Offline AI Brain with Rate Limit Failover
Uses free Groq endpoints for ultra-fast responses, and smoothly falls back
to offline local processing if API rate limits or network issues occur.
"""

import os
import json
import time
import re
from openai import OpenAI
import jarvis_actions

KEYFILE = os.path.expanduser("~/.openjarvis/free.key")
MODELS = ["openai/gpt-oss-20b", "llama-3.3-70b-versatile", "qwen/qwen3.8-27b"]

_client = None
_history = []
_cooldown_until = 0.0

SYSTEM_PROMPT = """You are Jarvis, a fast, highly capable AI executive assistant running on the user's computer.
Respond concisely in 1-2 sentences. Be direct, helpful, and natural. Do not use filler or formal corporate jargon.
LANGUAGE RULE: ALWAYS write your reply in English, even when the user speaks or writes Hindi, Telugu,
Tamil, Kannada, Malayalam, Bengali, Marathi, Gujarati, Punjabi, Odia, Urdu or any other language.
The user's audio is answered in their language by a separate voice layer - your written text must
stay English every single time. Never switch the written reply's language, never transliterate.
APP LAUNCHING: the local launcher opens applications and confirms real launches itself. If a
request to open/launch/start something reaches you WITHOUT such a confirmation, say plainly that
you could not open it. NEVER claim an app was opened, started or launched - that would be a lie.
"""


def _get_api_key():
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"].strip()
    if os.path.exists(KEYFILE):
        return open(KEYFILE).read().strip()
    return None


def ready():
    """Returns True if the cloud brain is active and not currently rate-limited."""
    key = _get_api_key()
    if not key:
        return False
    return time.time() >= _cooldown_until


def _is_rate_limited(e):
    status = getattr(e, "status_code", None)
    if status == 429:
        return True
    s = str(e).lower()
    return any(w in s for w in ("429", "rate limit", "too many requests", "rate_limit", "exceeded"))


def _retry_after_seconds(e, default=60):
    s = str(e).lower()
    m = re.search(r"try again in\s*(\d+)m\s*([0-9]+(?:\.[0-9]+)?)s", s)
    if m:
        return int(float(m.group(1)) * 60 + float(m.group(2))) + 2
    m = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)s", s)
    if m:
        return int(float(m.group(1))) + 2
    return default


def _set_cooldown(secs):
    global _cooldown_until
    _cooldown_until = max(_cooldown_until, time.time() + float(secs))


def _local_fallback(q):
    """
    Called when the cloud brain is unavailable (no key, rate limited or every
    model failed). Always returns a real answer - never an empty string.
    """
    action_res = jarvis_actions.try_action(q)
    if action_res:
        return action_res

    if not _get_api_key():
        return ("I'm running in local mode right now, so I can't reason about that. "
                "I can still open your apps, read the weather, the market and your inbox, "
                "and tell you the time.")
    if time.time() < _cooldown_until:
        return ("My cloud brain is cooling down from a rate limit, but the local assistant "
                "is active. I can open your apps, check the weather, analyze the market, "
                "or read your inbox while it recovers.")
    return ("I couldn't get an answer from any of my models just now. Try again in a moment, "
            "or ask me something local like the time, the weather or the market.")


def think(q):
    """
    Main entry point for queries. Tries local actions first, then cloud AI,
    with automatic fallback if rate limited.
    """
    global _client, _history

    # 1. Immediate local action check
    local_done = jarvis_actions.try_action(q)
    if not local_done:
        try:
            needs_translation = not q.isascii()
        except AttributeError:
            needs_translation = False
        if needs_translation:
            # Non-English command (Telugu, Tamil, Hindi, ...): translate once
            # so the local matchers answer with REAL local data (weather, time,
            # open app) instead of a generic cloud reply.
            import jarvis_lang
            english = jarvis_lang.to_english(q)
            if english:
                local_done = jarvis_actions.try_action(english)
                print(f"[LANG] action bridge: {english!r} -> "
                      f"{'MATCH' if local_done else 'no local match'}",
                      flush=True)
            else:
                print("[LANG] action bridge: translation unavailable", flush=True)
    if local_done:
        return local_done

    key = _get_api_key()
    if not key or time.time() < _cooldown_until:
        return _local_fallback(q)

    if _client is None:
        _client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")

    msgs = [{"role": "system", "content": SYSTEM_PROMPT}] + _history[-6:] + [{"role": "user", "content": q}]

    for model in MODELS:
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=msgs,
                max_tokens=350,
                temperature=0.6,
            )
            answer = (resp.choices[0].message.content or "").strip()
            if not answer:
                # reasoning models can burn the whole token budget on thinking
                print(f"[Model {model} returned empty content, trying next]")
                continue
            _history += [{"role": "user", "content": q}, {"role": "assistant", "content": answer}]
            return answer
        except Exception as e:
            if _is_rate_limited(e):
                wait_sec = _retry_after_seconds(e)
                _set_cooldown(wait_sec)
                print(f"[Rate-limit triggered: cooldown for {wait_sec}s]")
                return _local_fallback(q)
            print(f"[Model {model} failed: {e}]")

    return _local_fallback(q)


if __name__ == "__main__":
    print("Testing Jarvis Brain...")
    print(think("what time is it"))
    print(think("how are you"))
