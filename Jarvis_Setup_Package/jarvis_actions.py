"""
jarvis_actions.py - Local System Automation, WhatsApp, Mail & Stock Commands
Handles instant offline actions without hitting external LLM APIs.

Runs on Windows, macOS and Linux: URLs open with the default handler, apps are
discovered from the Start Menu / App Paths registry (Windows) or /Applications
(macOS), and volume is driven by pycaw (Windows) or osascript (macOS).
"""

import datetime
import glob
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

import jarvis_apps
import jarvis_mail
import jarvis_market
import jarvis_timers
import jarvis_weather

IS_WIN = sys.platform.startswith("win")

SITES = {
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "maps": "https://maps.google.com",
    "calendar": "https://calendar.google.com",
    "drive": "https://drive.google.com",
}

ALIASES = {
    "vs code": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "word": "Microsoft Word",
    "excel": "Microsoft Excel",
    "powerpoint": "Microsoft PowerPoint",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "terminal": "Terminal",
    "spotify": "Spotify",
}


# --------------------------------------------------------------------------- #
# Launching
# --------------------------------------------------------------------------- #
def _launch(target, args=None):
    """Open a URL, file or executable with the platform default handler."""
    try:
        if IS_WIN:
            if args:
                subprocess.Popen([target] + list(args))
            else:
                os.startfile(target)
            return True
        cmd = ["open"] + ([target] if not args else ["-a", target] + list(args))
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except Exception:
        return False


def _open(args):
    """Called with ['-a', app] for an app, or [url] for a URL/file."""
    if args and args[0] == "-a":
        return _launch(args[1])
    if not args:
        return False
    return _launch(args[0])


def _osa(script):
    """macOS AppleScript hook (volume). Returns False on other platforms."""
    if IS_WIN:
        return False
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True)
        return True
    except Exception:
        return False


def _win_volume(action):
    """Real Windows volume control via pycaw (comtypes ships with pyttsx3)."""
    try:
        from pycaw.pycaw import AudioUtilities
    except Exception:
        return False
    try:
        vol = AudioUtilities.GetSpeakers().EndpointVolume
        if action == "mute":
            vol.SetMute(1, None)
            return True
        if action == "unmute":
            vol.SetMute(0, None)
            return True
        if action == "toggle_mute":
            vol.SetMute(0 if vol.GetMute() else 1, None)
            return True
        current = vol.GetMasterVolumeLevelScalar()
        delta = 0.15 if action == "up" else -0.15
        vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, current + delta)), None)
        return True
    except Exception:
        return False


def _volume(action):
    if IS_WIN:
        return _win_volume(action)
    scripts = {
        "up": "set volume output volume ((output volume of (get volume settings)) + 15)",
        "down": "set volume output volume ((output volume of (get volume settings)) - 15)",
        "mute": "set volume with output muted",
        "unmute": "set volume with output muted false",
        "toggle_mute": "set volume output muted not (output muted of (get volume settings))",
    }
    return _osa(scripts[action]) if action in scripts else False


# --------------------------------------------------------------------------- #
# App discovery
# --------------------------------------------------------------------------- #
def _windows_apps():
    apps = {}
    try:
        import winreg

        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, base) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            name = winreg.EnumKey(key, i)
                            value, _ = winreg.QueryValueEx(key, name)
                        except OSError:
                            continue
                        if value:
                            apps.setdefault(os.path.splitext(name)[0].lower(),
                                            os.path.expandvars(value.strip('"')))
            except OSError:
                continue
    except Exception:
        pass

    for base_dir in (
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ):
        for path in glob.glob(os.path.join(base_dir, "**", "*.lnk"), recursive=True):
            name = os.path.splitext(os.path.basename(path))[0].lower()
            apps.setdefault(name, path)
    return apps


def _unix_apps():
    apps = {}
    for d in ("/Applications", "/Applications/Utilities", "/System/Applications",
              "/System/Applications/Utilities", os.path.expanduser("~/Applications")):
        for path in glob.glob(d + "/*.app"):
            apps[os.path.basename(path)[:-4].lower()] = path
    return apps


def _apps():
    return _windows_apps() if IS_WIN else _unix_apps()


def find_app(name):
    """Resolve a spoken name to a launchable path/id, or None."""
    wanted = ALIASES.get(name.lower().strip(), name)
    app = jarvis_apps.resolve(wanted)
    if app:
        return app["target"]

    name = wanted.lower().strip()
    apps = _apps()
    if name in apps:
        return apps[name]
    for key, value in apps.items():
        if name in key:
            return value
    if IS_WIN:
        for ext in (".exe", ".cmd", ".bat", ".com"):
            found = shutil.which(name + ext) or shutil.which(name)
            if found:
                return found
    else:
        found = shutil.which(name)
        if found:
            return found
    return None


_FILLER = re.compile(
    r"\b(please|kindly|for me|on (my|this|the) (computer|laptop|pc|machine|desktop)"
    r"|in (my|this|the) (computer|laptop|pc)|app|application|program|software)\b"
)


def _clean_request(name):
    """Strip filler words: 'open chrome for me on my computer' -> 'chrome'."""
    name = re.sub(r"^(the|my|a)\s+", "", (name or "").strip())
    cleaned = _FILLER.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?")
    return cleaned or name.strip()


def send_whatsapp_message(contact_name, message="Hello from Jarvis"):
    """Opens WhatsApp Web to message a contact."""
    encoded_msg = urllib.parse.quote(message)
    _open([f"https://web.whatsapp.com/send?text={encoded_msg}"])
    return f"Opening WhatsApp to message {contact_name}."


def get_mail_brief():
    """Real unread mail over IMAP when configured, else the local cache."""
    real = jarvis_mail.briefing()
    if real:
        return real

    cache_file = os.path.expanduser("~/.openjarvis/mail_cache.txt")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", errors="ignore") as f:
                content = f.read().strip()
                if content:
                    return "Here is your latest mail briefing: " + content[:600]
        except Exception:
            pass
    if jarvis_mail.is_configured():
        return "Mail is configured but the last check failed. Try again shortly."
    return ("You have 3 unread priority emails: team project sync, server alert, "
            "and a weekly client report. Add real mail with " + jarvis_mail.CONFIG_FILE)


_ALARM_RE = re.compile(
    r"(?:alarm|wake(?:\s+me)?(?:\s+up)?)\s*(?:at|for)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b"
    r"(?!\s*(?:\d+|minutes?|hours?|seconds?|mins?|hrs?|secs?))", re.I)

_NUMBERS = {"a": 1, "an": 1, "half": 0.5, "one": 1, "two": 2, "three": 3,
            "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
            "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60}

_DURATION_RE = re.compile(
    r"(\d+(?:\.\d+)?|half|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|eleven|twelve|twenty|thirty|forty|fifty|sixty|an|a)"
    r"\s*(?:(?:of|an?)\s+)?(hours?|hrs?|minutes?|mins?|seconds?|secs?)", re.I)

# "Hey Jarvis, open Chrome" -> the wake word is stripped, but "hey" stays and
# every anchored matcher (open/launch/run, search, ...) then misses - which is
# how the LLM ended up SAYING "Opening Instagram" without launching anything.
_PREFIXES = re.compile(
    r"^(?:hey+|hi|hello|yo|ok(?:ay)?|yeah|yep|yup|sure|right|so|now|just"
    r"|please|kindly|can you|could you|would you|will you"
    r"|i (?:want|need) to|let'?s)\b[\s,:!]*", re.I)


def _parse_duration(text):
    """'set a timer for 1 hour and 30 minutes' -> (5400, '1 hour and 30 minutes').
    Accepts digits, decimals and number words; needs an explicit unit."""
    total = 0.0
    first = last = None
    for m in _DURATION_RE.finditer(text):
        raw = m.group(1).lower()
        try:
            number = float(raw)
        except ValueError:
            number = _NUMBERS.get(raw)
        if number is None:
            continue
        unit = m.group(2).lower()
        if unit.startswith("hour") or unit.startswith("hr"):
            total += number * 3600
        elif unit.startswith("min"):
            total += number * 60
        else:
            total += number
        if first is None:
            first = m.start()
        last = m.end()
    if total <= 0 or first is None:
        return None, None
    return int(total), text[first:last].strip()


def _next_alarm(hour, minute, ampm):
    """Next future occurrence of HH:MM (today if still ahead, else tomorrow)."""
    if minute > 59 or hour > 23:
        return None
    now = datetime.datetime.now()
    if ampm:
        hours = [hour % 12 + (12 if ampm == "pm" else 0)]
    else:
        hours = [hour] + ([hour + 12] if hour <= 12 else [])
    for hh in hours:
        if hh > 23:
            continue
        candidate = now.replace(hour=hh, minute=minute, second=0, microsecond=0)
        if candidate > now:
            return candidate
    return (now + datetime.timedelta(days=1)).replace(
        hour=hours[0], minute=minute, second=0, microsecond=0)


def try_action(q):
    """
    Executes instant deterministic actions. Returns response string if handled,
    or None to pass to the AI brain.
    """
    q_clean = q.lower().strip(" .,!?")

    # Peel conversational fillers until the command starts with a real verb.
    # One pass is not enough for "okay, yeah, open chrome".
    for _ in range(3):
        peeled = _PREFIXES.sub("", q_clean).strip()
        if peeled == q_clean:
            break
        q_clean = peeled

    # 1. Stock Market Queries & Buy/Sell Analysis
    if any(k in q_clean for k in ("stock", "market", "nifty", "sensex", "crypto",
                                  "bitcoin", "buy or sell")):
        for sym, name in jarvis_market.DEFAULT_TICKERS.items():
            if name.lower() in q_clean or sym.lower().replace(".ns", "") in q_clean:
                data = jarvis_market.analyze_ticker(sym)
                if data:
                    direction = "up" if data["pct_change"] >= 0 else "down"
                    return (
                        f"{data['name']} is trading at {data['currency']}{data['price']:,}, "
                        f"{direction} {abs(data['pct_change'])}%. "
                        f"Technical indicator: {data['signal']}. {data['reason']}"
                    )
        return "Here is the market overview: " + jarvis_market.market_briefing()

    # 2. Weather
    if any(k in q_clean for k in ("weather", "forecast", "temperature outside",
                                  "how hot", "how cold", "will it rain", "rain today")):
        return jarvis_weather.briefing() or "I could not fetch the weather right now."

    # 3. WhatsApp Auto-Message
    m = re.search(r"(?:send|reply|message)\s+(?:on\s+whatsapp\s+to|whatsapp\s+to|to)\s+(.+?)(?:\s+saying\s+(.+))?$",
                  q_clean)
    if "whatsapp" in q_clean and m:
        contact = m.group(1).strip()
        msg = m.group(2).strip() if m.group(2) else "I received your message, will get back shortly."
        return send_whatsapp_message(contact, msg)

    if q_clean in ("open whatsapp", "check whatsapp", "whatsapp"):
        _open([SITES["whatsapp"]])
        return "Opening WhatsApp."

    # 3. Which apps can you open?
    if any(k in q_clean for k in ("list my apps", "list apps", "what apps", "show my apps",
                                  "which apps", "app list", "apps available",
                                  "what can you open")):
        return jarvis_apps.summary(limit=8)

    # 4. Mail Briefing
    if any(k in q_clean for k in ("mail brief", "check mail", "read emails",
                                  "inbox summary", "unread emails", "check my mail",
                                  "check my inbox")):
        return get_mail_brief()

    # 5. YouTube Music / Search
    m = re.search(r"(?:play|search|find|open)\s+(.+?)\s+(?:on|in)\s+youtube", q_clean)
    if m:
        query = m.group(1).strip()
        _open(["https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)])
        return f"Playing {query} on YouTube."

    # 6. Google Search
    m = re.match(r"(?:search|google)(?: for)?\s+(.+)", q_clean)
    if m:
        query = m.group(1).strip()
        _open(["https://www.google.com/search?q=" + urllib.parse.quote(query)])
        return f"Searching Google for {query}."

    # 7. Timers & Alarms - runs BEFORE App Launch: "start a timer" is a timer,
    # never "start the app named timer", and "time left" must not hit the clock.
    _opens_app = re.match(r"^(?:please\s+)?(?:open|launch|run)\s+", q_clean)
    if not (_opens_app and " for " not in q_clean) and any(
            w in q_clean for w in ("timer", "alarm", "wake me", "wake up")):
        if any(w in q_clean for w in
               ("cancel", "stop", "delete", "clear", "dismiss")):
            count = jarvis_timers.cancel_all()
            return ("All timers and alarms cancelled." if count
                    else "No timer or alarm is running.")
        if any(w in q_clean for w in
               ("status", "left", "how long", "how much time",
                "still running", "how many timers", "when does")):
            return jarvis_timers.status_text() or "No timer or alarm is running."

        m = _ALARM_RE.search(q_clean)
        if m:
            when = _next_alarm(int(m.group(1)), int(m.group(2) or 0),
                               (m.group(3) or "").lower() or None)
            if when is None:
                return ("That time does not look valid. "
                        "Say for example, alarm at 7:30 am.")
            seconds = (when - datetime.datetime.now()).total_seconds()
            label = when.strftime("%I:%M %p").lstrip("0")
            jarvis_timers.start(max(1, int(seconds)), label, kind="alarm")
            day = "" if when.date() == datetime.datetime.now().date() \
                else " tomorrow"
            return f"Alarm set for {label}{day}."

        seconds, phrase = _parse_duration(q_clean)
        if seconds:
            label = re.sub(r"s$", "", phrase.strip()) + " timer"
            jarvis_timers.start(seconds, label)
            return f"Timer set for {phrase}."

        if "timer" in q_clean:
            return "For how long? Say for example, set a timer for 10 minutes."
        return "What time should the alarm be? Say for example, alarm at 7:30 am."

    # 8. App Launch
    m = re.match(r"^(?:please\s+)?(?:open|launch|start|run)\s+(.+)$", q_clean)
    if m:
        name = _clean_request(m.group(1))
        if name in SITES:
            _open([SITES[name]])
            return f"Opening {name}."

        wanted = ALIASES.get(name, name)
        app = jarvis_apps.resolve(wanted)
        if app and jarvis_apps.launch(app):
            return f"Opening {app['name']}."

        legacy = find_app(name)
        if legacy and _open(["-a", legacy]):
            lowered = str(legacy).lower()
            label = os.path.basename(legacy) if lowered.endswith((".lnk", ".exe")) else legacy
            return f"Opening {label}."
        return f"I couldn't find an application named {name}."

    # 9. Volume Controls
    if "volume up" in q_clean or "louder" in q_clean:
        return "Volume increased." if _volume("up") else "I could not change the volume."
    if "volume down" in q_clean or "quieter" in q_clean:
        return "Volume decreased." if _volume("down") else "I could not change the volume."
    if q_clean in ("mute", "mute volume", "mute the volume"):
        return "System audio muted." if _volume("mute") else "I could not mute the audio."
    if q_clean in ("unmute", "unmute volume"):
        return "System audio unmuted." if _volume("unmute") else "I could not unmute the audio."

    # 10. Date and Time
    if "time" in q_clean:
        return "It is currently " + datetime.datetime.now().strftime("%I:%M %p")
    if "date" in q_clean or "today" in q_clean:
        return "Today is " + datetime.datetime.now().strftime("%A, %B %d, %Y")

    return None
