"""
jarvis_state.py - Single source of truth shared by the voice loop, the local
HTTP API and the holographic HUD.

Everything is guarded by one lock so the listening thread (main), the HTTP
server thread and the cache-refresh code can all touch it safely.
"""

import json
import time
import threading

_lock = threading.Lock()
# Serialises brain calls so the mic loop and the HTTP API never interleave.
brain_lock = threading.Lock()

_STATE = {
    # LISTENING | PROCESSING | SPEAKING | OFFLINE
    "status": "LISTENING",
    # 0.0-1.0, most recent microphone energy reading (drives the oscilloscope)
    "level": 0.0,
    # Conversation window: until this epoch the mic accepts commands WITHOUT
    # the wake word. 0.0 = closed (wake word required for everything).
    "followup_until": 0.0,
    # Last time the wake word was actually heard (epoch). The HUD turns this
    # into a visible LISTENING acknowledgement so the user knows it heard them.
    "last_wake_at": 0.0,
    # Live timers/alarms: [{id, kind, label, ends_at, total}] - the Dynamic
    # Island counts these down from here.
    "timers": [],
    # [{who: "you"|"jarvis", text: str, t: epoch_seconds}]
    "messages": [],
    # lazily filled caches: {"at": epoch, "data": ...}
    "market": {"at": 0.0, "data": None},
    "weather": {"at": 0.0, "data": None},
    "mail": {"at": 0.0, "data": None},
    "vitals": {"at": 0.0, "data": None},
    "brain": {"ready": False, "cooldown_until": 0.0, "model": None},
    "started_at": time.time(),
    "seq": 0,
}

_MAX_MESSAGES = 60


def _touch():
    _STATE["seq"] += 1


def set_status(status):
    with _lock:
        if _STATE["status"] != status:
            _STATE["status"] = status
            _touch()


def set_level(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return
    with _lock:
        _STATE["level"] = max(0.0, min(1.0, value))


def arm_followup(seconds):
    """Open the conversation window: for the next `seconds` the microphone
    accepts commands without hearing the wake word again. Only ever extends
    the window - a short re-arm can never cut a live conversation short.
    Returns the new window end (epoch seconds)."""
    ts = time.time() + max(0.0, float(seconds))
    with _lock:
        if ts > _STATE.get("followup_until", 0.0):
            _STATE["followup_until"] = ts
            _touch()
    return ts


def followup_active(now=None):
    with _lock:
        return (now or time.time()) < _STATE.get("followup_until", 0.0)


def followup_remaining(now=None):
    with _lock:
        return max(0.0, _STATE.get("followup_until", 0.0) - (now or time.time()))


def note_wake():
    """The wake word was heard - the HUD shows a LISTENING acknowledgement."""
    with _lock:
        _STATE["last_wake_at"] = time.time()
        _touch()


def set_timers(items):
    """Replace the live timer list (called by jarvis_timers on every change)."""
    with _lock:
        _STATE["timers"] = list(items)
        _touch()


def set_brain(**kwargs):
    with _lock:
        _STATE["brain"].update(kwargs)
        _touch()


def add_message(who, text):
    if not text:
        return
    with _lock:
        _STATE["messages"].append({"who": who, "text": str(text), "t": time.time()})
        if len(_STATE["messages"]) > _MAX_MESSAGES:
            del _STATE["messages"][: len(_STATE["messages"]) - _MAX_MESSAGES]
        _touch()


def put_cache(section, data):
    """Store a freshly fetched payload in one of market/weather/mail/vitals."""
    with _lock:
        bucket = _STATE.get(section)
        if bucket is None:
            return
        bucket["at"] = time.time()
        bucket["data"] = data
        _touch()


def get_cache(section, max_age):
    """Return (data, age_seconds) if younger than max_age, else (None, age)."""
    with _lock:
        bucket = _STATE.get(section) or {}
        age = time.time() - bucket.get("at", 0.0)
        data = bucket.get("data")
    if data is None or age > max_age:
        return None, age
    return data, age


def snapshot():
    """Deep copy as plain JSON-safe data, for serialising to the HUD."""
    with _lock:
        return json.loads(json.dumps(_STATE, default=str))


def status_text():
    with _lock:
        return _STATE["status"]
