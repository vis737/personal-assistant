"""
jarvis_timers.py - Voice timers and alarms, published live to the HUD and the
Dynamic Island.

    start(600, "10 minute timer")            -> speaks "Your 10 minute timer is done."
    start(epoch, "7:30 PM", kind="alarm")    -> speaks when it rings
    cancel_all()                             -> stops everything, returns count
    status_text()                            -> "Your 10 minute timer has 4 minutes left."

Every timer lives in `jarvis_state` (set_timers), so the island, the HUD and
the API all read one source of truth. Each timer runs on its own daemon
thread waiting on an Event, so cancellation is instant and a fired timer
speaks through the normal language-aware voice pipeline.
"""

import datetime
import threading
import time
import uuid

import jarvis_state

_lock = threading.Lock()
_timers = {}


def _public(timer):
    return {k: timer[k] for k in ("id", "kind", "label", "ends_at", "total")}


def _publish():
    with _lock:
        items = sorted((_public(t) for t in _timers.values()),
                       key=lambda t: t["ends_at"])
    jarvis_state.set_timers(items)


def humanize(seconds):
    """412 -> '6 minutes and 52 seconds' (never says '0 seconds' alone)."""
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
    if minutes:
        parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
    if secs or not parts:
        parts.append(f"{secs} second" + ("s" if secs != 1 else ""))
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0]


def start(seconds, label="timer", kind="timer"):
    """Arm one timer. `seconds` is a duration; `ends_at` is computed here so
    every consumer shows the same deadline."""
    seconds = max(1, int(seconds))
    timer_id = uuid.uuid4().hex[:8]
    event = threading.Event()
    with _lock:
        _timers[timer_id] = {
            "id": timer_id, "kind": kind, "label": label,
            "ends_at": time.time() + seconds, "total": seconds,
            "_event": event,
        }
    _publish()

    def run():
        if event.wait(seconds):
            return          # cancelled before it could fire
        _fire(timer_id)

    threading.Thread(target=run, daemon=True).start()
    return timer_id


def _fire(timer_id):
    with _lock:
        info = _timers.pop(timer_id, None)
    _publish()
    if not info:
        return
    if info["kind"] == "alarm":
        now = datetime.datetime.now().strftime("%I:%M %p").lstrip("0")
        message = f"It is {now}. Your {info['label']} alarm is ringing."
    else:
        message = f"Your {info['label']} is done."
    try:
        jarvis_state.add_message("jarvis", message)
        import jarvis_voice            # lazy: voice imports the brain chain
        jarvis_voice.speak(message)
    except Exception as exc:
        print(f"[timer fired, voice unavailable]: {message} ({exc})")


def cancel_all(kind=None):
    """Cancel every timer (or one kind: 'timer' / 'alarm'). Returns count."""
    with _lock:
        ids = [tid for tid, t in _timers.items()
               if kind is None or t["kind"] == kind]
        for tid in ids:
            _timers[tid]["_event"].set()
            del _timers[tid]
    _publish()
    return len(ids)


def active(kind=None):
    with _lock:
        return sorted((_public(t) for t in _timers.values()
                       if kind is None or t["kind"] == kind),
                      key=lambda t: t["ends_at"])


def status_text():
    """Spoken status of the next deadline, or None when nothing is armed."""
    items = active()
    if not items:
        return None
    first = items[0]
    left = humanize(first["ends_at"] - time.time())
    if first["kind"] == "alarm":
        head = f"Your alarm for {first['label']} goes off in {left}."
    else:
        head = f"Your {first['label']} has {left} left."
    if len(items) > 1:
        head += f" {len(items)} timers are running."
    return head