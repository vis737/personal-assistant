"""
jarvis_island.py - Dynamic Island overlay for Jarvis (top-center, always on top).

A small black pill floats at the top-center of the screen and grows into a
live-activity panel whenever something happens:

  compact   status dot, J.A.R.V.I.S, follow-up countdown, live timer or clock
  expanded  big LISTENING/SPEAKING status, last reply, voice level bars,
            language chip, timer countdown

Left click  -> open the full HUD in the browser
Right click -> menu (Open HUD / Exit)
Engine down -> red dot, "ENGINE OFFLINE", keeps retrying silently.

Zero new dependencies: tkinter + urllib poll http://127.0.0.1:8787/api/state.
Single instance via logs/island.pid (so the launcher can start it blindly).
Run `jarvis_island.py --check` for a one-shot self test.
"""

import datetime
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

import tkinter as tk

API = "http://127.0.0.1:8787/api/state"
HUD_URL = "http://127.0.0.1:8080/"

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "logs")
LOG_FILE = os.path.join(LOGS, "island.log")
PID_FILE = os.path.join(LOGS, "island.pid")
os.makedirs(LOGS, exist_ok=True)

# Transparent colour key: everything painted in this colour disappears, which
# is how the pill gets its rounded corners without a window frame.
TRANS = "#010203"
PILL = "#0a0c12"
EDGE = "#1c2230"
CYAN = "#5fe3ff"
GOLD = "#ffc14d"
GREEN = "#3adf9a"
RED = "#e0524a"
MUTED = "#7d879b"
LIGHT = "#cdd6e4"

COMPACT_W, COMPACT_H = 372, 46
EXPANDED_W, EXPANDED_H = 470, 116
TOP_MARGIN = 10
POLL_MS = 450
ANIM_STEP_MS = 25
ANIM_PX = 7
WAKE_FLASH_S = 3.5

LANG_NAMES = {
    "en-IN": "English", "hi-IN": "Hindi", "te-IN": "Telugu",
    "ta-IN": "Tamil", "kn-IN": "Kannada", "ml-IN": "Malayalam",
    "bn-IN": "Bengali", "mr-IN": "Marathi", "gu-IN": "Gujarati",
    "pa-IN": "Punjabi", "or-IN": "Odia", "ur-IN": "Urdu",
}

_shared = {"data": None, "ok": False}


def log(message):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except Exception:
        pass


def acquire_lock():
    """Single instance: only the process owning island.pid may run."""
    try:
        if os.path.exists(PID_FILE):
            old = int(open(PID_FILE).read().strip() or 0)
            if old and old != os.getpid():
                import psutil
                if psutil.pid_exists(old):
                    return False
    except Exception:
        pass
    try:
        with open(PID_FILE, "w") as handle:
            handle.write(str(os.getpid()))
    except Exception:
        pass
    return True


def release_lock():
    try:
        if os.path.exists(PID_FILE):
            mine = int(open(PID_FILE).read().strip() or 0)
            if mine == os.getpid():
                os.remove(PID_FILE)
    except Exception:
        pass


def poll():
    """Background loop: keeps the latest engine state in _shared."""
    while True:
        try:
            with urllib.request.urlopen(API, timeout=1.5) as response:
                _shared["data"] = json.loads(response.read().decode("utf-8"))
            _shared["ok"] = True
        except Exception:
            _shared["ok"] = False
        time.sleep(POLL_MS / 1000)


# --------------------------------------------------------------------------- #
# Drawing helpers
# --------------------------------------------------------------------------- #
def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def countdown_text(epoch, now):
    seconds = max(0, int(epoch - now))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def analyse():
    """Turn raw engine state into the few facts the island draws."""
    data = _shared["data"] if _shared["ok"] else None
    now = time.time()
    info = {
        "online": data is not None,
        "status": (data or {}).get("status", "OFFLINE"),
        "level": float((data or {}).get("level") or 0.0),
        "language": LANG_NAMES.get((data or {}).get("language") or "", "English"),
        "woke": 0.0,
        "follow_left": 0.0,
        "timer": None,
        "reply": "",
    }
    if not data:
        return info
    info["woke"] = max(0.0, WAKE_FLASH_S - (now - float(data.get("last_wake_at") or 0)))
    info["follow_left"] = max(0.0, float(data.get("followup_until") or 0) - now)
    timers = data.get("timers") or []
    if timers:
        info["timer"] = countdown_text(timers[0]["ends_at"], now)
    for message in reversed(data.get("messages") or []):
        if message.get("who") == "jarvis" and message.get("text"):
            info["reply"] = str(message["text"])[:150]
            break
    return info


def status_color(info):
    if not info["online"]:
        return RED
    if info["woke"] > 0:
        return GOLD
    return {"SPEAKING": CYAN, "PROCESSING": GOLD, "LISTENING": GREEN}.get(
        info["status"], MUTED)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
class Island:
    def __init__(self, root):
        self.root = root
        self.expanded = False
        self.cur_w, self.cur_h = COMPACT_W, COMPACT_H
        self.pulse = 0

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", TRANS)
        root.configure(bg=TRANS)

        self.canvas = tk.Canvas(root, bg=TRANS, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", lambda _e: self.open_hud())

        self.menu = tk.Menu(root, bg=PILL, fg=LIGHT,
                            activebackground=EDGE, activeforeground=CYAN,
                            tearoff=0, borderwidth=0)
        self.menu.add_command(label="Open Jarvis HUD", command=self.open_hud)
        self.menu.add_command(label="Exit island", command=self.quit)
        self.canvas.bind("<Button-3>",
                         lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        self.root.bind("<Escape>", lambda _e: self.quit())

    # ---- actions ---------------------------------------------------------- #
    def open_hud(self):
        webbrowser.open(HUD_URL)

    def quit(self):
        self.root.destroy()

    # ---- geometry --------------------------------------------------------- #
    def place(self, width, height):
        screen = self.root.winfo_screenwidth()
        x = max(0, (screen - width) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{TOP_MARGIN}")

    def animate_towards(self, target_w, target_h):
        if (self.cur_w, self.cur_h) == (target_w, target_h):
            return
        self.cur_w += max(-ANIM_PX, min(ANIM_PX, target_w - self.cur_w))
        self.cur_h += max(-ANIM_PX, min(ANIM_PX, target_h - self.cur_h))
        self.place(int(self.cur_w), int(self.cur_h))
        if (self.cur_w, self.cur_h) != (target_w, target_h):
            self.root.after(ANIM_STEP_MS,
                            lambda: self.animate_towards(target_w, target_h))

    # ---- drawing ---------------------------------------------------------- #
    def draw(self, info):
        canvas = self.canvas
        canvas.delete("all")
        w, h = int(self.cur_w), int(self.cur_h)
        round_rect(canvas, 1, 1, w - 2, h - 2, h // 2,
                   fill=PILL, outline=EDGE, width=1)

        if self.expanded:
            self._draw_expanded(canvas, info, w, h)
        else:
            self._draw_compact(canvas, info, w, h)

    def _dot(self, canvas, info, x, y):
        color = status_color(info)
        radius = 5 + (1 if int(self.pulse) % 2 == 0 and info["online"] else 0)
        canvas.create_oval(x - radius, y - radius, x + radius, y + radius,
                           fill=color, outline="")

    def _draw_compact(self, canvas, info, w, h):
        y = h // 2
        self._dot(canvas, info, 22, y)
        canvas.create_text(38, y, anchor="w", text="J.A.R.V.I.S",
                           fill=CYAN, font=("Segoe UI", 10, "bold"))

        if info["woke"] > 0:
            label, color = "LISTENING", GOLD
        elif not info["online"]:
            label, color = "ENGINE OFFLINE", RED
        elif info["timer"]:
            label, color = f"TIMER {info['timer']}", GOLD
        else:
            label, color = datetime.datetime.now().strftime("%H:%M"), MUTED
        canvas.create_text(w - 16, y, anchor="e", text=label,
                           fill=color, font=("Segoe UI", 9, "bold"))

        if info["follow_left"] > 0:
            canvas.create_text(w // 2 + 30, y, anchor="e",
                               text=f"OPEN {int(info['follow_left'])}s",
                               fill=GOLD, font=("Segoe UI", 8, "bold"))

    def _draw_expanded(self, canvas, info, w, h):
        left = 22
        # row 1: big status + language chip
        if info["woke"] > 0:
            head, color = "LISTENING", GOLD
        else:
            head, color = info["status"], CYAN if info["status"] != "OFFLINE" else RED
        canvas.create_text(left, 30, anchor="w", text=head,
                           fill=color, font=("Segoe UI", 13, "bold"))
        right = w - 18
        if info["online"]:
            canvas.create_text(right, 30, anchor="e", text=info["language"],
                               fill=GOLD, font=("Segoe UI", 9, "bold"))
            right -= 16 + 7 * len(info["language"])
        if info["timer"]:
            canvas.create_text(right, 30, anchor="e",
                               text=f"TIMER {info['timer']}",
                               fill=GOLD, font=("Segoe UI", 9, "bold"))

        # row 2: last reply excerpt
        reply = info["reply"] or "Standing by, sir."
        canvas.create_text(left, 62, anchor="w", text=reply,
                           fill=LIGHT, font=("Segoe UI", 9),
                           width=w - left - 20, justify="left")

        # row 3: voice level bars + follow-up chip
        base_y = h - 16
        for i in range(9):
            x = left + i * 11
            swing = abs((self.pulse * 0.6 + i * 0.9) % 3.14)
            height = 4 + info["level"] * 26 * (0.5 + 0.5 * abs(swing - 1.57) / 1.57)
            canvas.create_rectangle(x, base_y - height, x + 6, base_y,
                                    fill=CYAN if info["level"] > 0.02 else EDGE,
                                    outline="")
        if info["follow_left"] > 0:
            canvas.create_text(w - 18, base_y - 6, anchor="e",
                               text=f"OPEN {int(info['follow_left'])}s",
                               fill=GOLD, font=("Segoe UI", 9, "bold"))

    # ---- main loop -------------------------------------------------------- #
    def tick(self):
        info = analyse()
        want_expanded = info["online"] and (
            info["status"] in ("PROCESSING", "SPEAKING") or info["woke"] > 0)

        if want_expanded != self.expanded:
            self.expanded = want_expanded
            target = ((EXPANDED_W, EXPANDED_H) if self.expanded
                      else (COMPACT_W, COMPACT_H))
            self.animate_towards(*target)
        elif (int(self.cur_w), int(self.cur_h)) != (
                EXPANDED_W if self.expanded else COMPACT_W,
                EXPANDED_H if self.expanded else COMPACT_H):
            target = ((EXPANDED_W, EXPANDED_H) if self.expanded
                      else (COMPACT_W, COMPACT_H))
            self.animate_towards(*target)

        self.pulse += 1
        self.draw(info)
        self.root.after(1000 if not info["online"] else 400, self.tick)


def run_check():
    """One-shot self test used by the installer/verification: fetch, draw, exit."""
    try:
        with urllib.request.urlopen(API, timeout=2) as response:
            _shared["data"] = json.loads(response.read().decode("utf-8"))
        _shared["ok"] = True
    except Exception as exc:
        print(f"island check: engine unreachable ({exc}) - drawing offline state")
    root = tk.Tk()
    Island(root)
    root.update_idletasks()
    info = analyse()
    print(f"ISLAND CHECK OK | online={info['online']} status={info['status']} "
          f"language={info['language']} timers={len((_shared['data'] or {}).get('timers') or [])}")
    root.destroy()
    return 0


def main():
    if "--check" in sys.argv:
        return run_check()

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    if not acquire_lock():
        log("another island is already running - exiting")
        return 0

    threading.Thread(target=poll, daemon=True).start()
    log("island starting")

    try:
        root = tk.Tk()
    except Exception as exc:
        log(f"tkinter unavailable: {exc}")
        release_lock()
        return 1

    app = Island(root)
    app.place(COMPACT_W, COMPACT_H)
    root.after(200, app.tick)
    try:
        root.mainloop()
    finally:
        release_lock()
        log("island exited")
    return 0


if __name__ == "__main__":
    sys.exit(main())