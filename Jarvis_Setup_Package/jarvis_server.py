"""
jarvis_server.py - Local HTTP API that hosts the whole assistant.

Runs in ONE process:
  * a daemon thread serving http://127.0.0.1:8787 for the holographic HUD
  * a background refresher keeping market / weather / mail / vitals warm
  * the main thread running the microphone loop (jarvis_voice.listen_loop)

Endpoints
  GET  /api/state     live status, transcript, vitals, market, weather, mail
  GET  /api/market    Yahoo Finance watchlist
  GET  /api/weather   Open-Meteo forecast (keyless)
  GET  /api/mail      unread IMAP briefing (falls back when unconfigured)
  GET  /api/healthz   liveness probe
  POST /api/chat      {"text": "..."} -> brain -> spoken reply

Bind is loopback-only: nothing here is reachable from the network.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psutil

import jarvis_brain
import jarvis_lang
import jarvis_mail
import jarvis_market
import jarvis_state
import jarvis_weather
from jarvis_voice import handle_query

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_PORT", "8787"))

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}

_VITALS_TTL = 5
_MARKET_TTL = 300
_MAIL_TTL = 300
_WEATHER_TTL = 900


def _vitals(force=False):
    if not force:
        cached, _ = jarvis_state.get_cache("vitals", _VITALS_TTL)
        if cached:
            return cached
    data = {
        "cpu": psutil.cpu_percent(interval=0.05),
        "mem": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage(os.environ.get("SystemDrive", "C:")).percent,
        "uptime": int(time.time() - psutil.boot_time()),
        "battery": None,
    }
    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            data["battery"] = battery.percent
    except Exception:
        pass
    jarvis_state.put_cache("vitals", data)
    return data


def _refresh_market():
    try:
        jarvis_state.put_cache("market", {"items": jarvis_market.get_market_watchlist() or []})
    except Exception as exc:
        print(f"[market refresh failed]: {exc}")


def _warm(force=False):
    """Populate every cache. Called on a timer, never from a request handler."""
    try:
        _vitals(force=force)
    except Exception as exc:
        print(f"[vitals failed]: {exc}")
    cached, _ = jarvis_state.get_cache("market", _MARKET_TTL)
    if cached is None:
        _refresh_market()
    try:
        jarvis_weather.current(force=force)
    except Exception as exc:
        print(f"[weather failed]: {exc}")
    try:
        jarvis_mail.unread(force=force)
    except Exception as exc:
        print(f"[mail failed]: {exc}")


def _refresher():
    while True:
        _warm()
        time.sleep(15)


def _state_payload():
    payload = jarvis_state.snapshot()
    payload["language"] = jarvis_lang.user_language()
    # _vitals() self-refreshes past its short TTL; the raw cache would read None
    # for the 10s between background refreshes.
    payload["vitals"] = _vitals()
    payload["market"] = jarvis_state.get_cache("market", _MARKET_TTL)[0]
    payload["weather"] = jarvis_state.get_cache("weather", _WEATHER_TTL)[0]
    payload["mail"] = jarvis_state.get_cache("mail", _MAIL_TTL)[0]
    payload["brain"] = dict(payload.get("brain") or {},
                            ready=jarvis_brain.ready(),
                            cooldown_until=jarvis_brain._cooldown_until)
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis/2.0"

    def log_message(self, fmt, *args):  # keep the console/log clean
        pass

    def _send(self, code, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in _CORS.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for key, value in _CORS.items():
            self.send_header(key, value)
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/api/state", "/api/"):
            self._send(200, _state_payload())
        elif path == "/api/market":
            self._send(200, jarvis_state.get_cache("market", _MARKET_TTL)[0] or {"items": []})
        elif path == "/api/weather":
            self._send(200, jarvis_weather.current() or {"ok": False})
        elif path == "/api/mail":
            self._send(200, jarvis_mail.unread() or {"ok": False})
        elif path == "/api/healthz":
            self._send(200, {"ok": True, "status": jarvis_state.status_text(),
                             "ready": jarvis_brain.ready()})
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/chat":
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            text = (json.loads(raw.decode("utf-8", "ignore")) or {}).get("text", "")
        except Exception:
            self._send(400, {"ok": False, "error": "bad json"})
            return

        text = (text or "").strip()
        if not text:
            self._send(400, {"ok": False, "error": "empty text"})
            return

        reply = None
        try:
            reply = handle_query(text)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self._send(500, {"ok": False, "error": str(exc), "reply": ""})
            jarvis_state.set_status("LISTENING")
            return
        self._send(200, {"ok": True, "reply": reply or "", "state": _state_payload()})


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    jarvis_state.set_status("LISTENING")
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"[Jarvis] API could not bind {HOST}:{PORT} ({exc}) - continuing voice only.")
        httpd = None
    if httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        print(f"[Jarvis] API ready at http://{HOST}:{PORT}")

    threading.Thread(target=_refresher, daemon=True).start()
    threading.Thread(target=_warm, kwargs={"force": True}, daemon=True).start()

    try:
        jarvis_voice_loop()
    finally:
        jarvis_state.set_status("OFFLINE")
        if httpd:
            httpd.shutdown()


def jarvis_voice_loop():
    import jarvis_voice
    jarvis_voice.listen_loop()


if __name__ == "__main__":
    sys.exit(main())
