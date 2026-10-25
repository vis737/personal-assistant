"""
jarvis_apps.py - Find and launch any application installed on this computer.

Every source is merged into one cached index so "open X" can match whatever the
user actually calls the program:

  1. Start Menu shortcuts   (current user + all users)      -> .lnk
  2. Desktop shortcuts      (current user + Public)         -> .lnk
  3. App Paths registry     (HKLM / HKCU)                   -> .exe
  4. PATH executables                                     -> .exe
  5. Program Files trees    (depth-limited walk)            -> .exe
  6. Microsoft Store / UWP  (Get-StartApps)                 -> shell:AppsFolder\\<id>

Matching is forgiving: exact -> prefix -> substring -> every-token -> fuzzy
(difflib), because nobody says "Visual Studio Code" out loud.
"""

import difflib
import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time

IS_WIN = sys.platform.startswith("win")
CACHE_SECONDS = 600

_index = {"apps": [], "by_name": {}, "at": 0.0}
_lock = threading.Lock()


def _norm(name):
    """'Visual Studio Code.lnk' -> 'visual studio code'"""
    name = os.path.splitext(str(name))[0] if str(name).lower().endswith(
        (".lnk", ".exe", ".app", ".bat", ".cmd", ".com")) else str(name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9一-鿿\s+#.]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _add(store, seen, name, target, kind, priority):
    key = _norm(name)
    if not key or not target:
        return
    existing = seen.get(key)
    if existing is None or priority < existing["priority"]:
        app = {"name": name.strip(), "key": key, "target": target, "kind": kind,
               "priority": priority}
        seen[key] = app


def _start_menu_links():
    dirs = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for base in dirs:
        for path in glob.glob(os.path.join(base, "**", "*.lnk"), recursive=True):
            yield os.path.splitext(os.path.basename(path))[0], path, "lnk"


def _desktop_links():
    for base in (os.path.expandvars(r"%USERPROFILE%\Desktop"),
                 os.path.expandvars(r"%PUBLIC%\Desktop")):
        for path in glob.glob(os.path.join(base, "*.lnk")):
            yield os.path.splitext(os.path.basename(path))[0], path, "lnk"


def _app_paths():
    try:
        import winreg
    except Exception:
        return
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
                        yield (os.path.splitext(name)[0],
                               os.path.expandvars(value.strip('"')), "exe")
        except OSError:
            continue


def _path_executables():
    seen_dirs = set()
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        if not entry or entry in seen_dirs:
            continue
        seen_dirs.add(entry)
        for ext in (".exe", ".cmd", ".bat", ".com"):
            for path in glob.glob(os.path.join(entry, "*" + ext)):
                yield os.path.splitext(os.path.basename(path))[0], path, "exe"


def _program_files(limit=6000):
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    count = 0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        root_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath.rstrip(os.sep).count(os.sep) - root_depth
            if depth >= 3:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not d.lower().endswith(
                ("unins", "uninstall", "$windows.~bt"))]
            for filename in filenames:
                if filename.lower().endswith(".exe") and not filename.lower().startswith(
                        ("unins", "uninstall", "setup", "vc_redist")):
                    count += 1
                    if count > limit:
                        return
                    yield os.path.splitext(filename)[0], os.path.join(dirpath, filename), "exe"


def _uwp_apps():
    """Microsoft Store / UWP apps, which have no .lnk to launch."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
            capture_output=True, text=True, timeout=25, creationflags=0x08000000,
        )
    except Exception:
        return
    for line in (out.stdout or "").splitlines():
        if "|" not in line:
            continue
        name, appid = line.split("|", 1)
        name, appid = name.strip(), appid.strip()
        if name and appid and not appid.lower().endswith((".lnk", ".exe")):
            yield name, appid, "uwp"


def collect(force=False):
    """Build (or return) the merged, cached application index."""
    now = time.time()
    with _lock:
        if not force and _index["apps"] and now - _index["at"] < CACHE_SECONDS:
            return _index["apps"]

    seen = {}
    if IS_WIN:
        # Priority order: Start Menu naming beats raw exe hunting.
        for name, target, kind in _start_menu_links():
            _add(seen, seen, name, target, kind, 1)
        for name, target, kind in _desktop_links():
            _add(seen, seen, name, target, kind, 2)
        for name, target, kind in _app_paths():
            _add(seen, seen, name, target, kind, 3)
        for name, target, kind in _path_executables():
            _add(seen, seen, name, target, kind, 4)
        for name, target, kind in _program_files():
            _add(seen, seen, name, target, kind, 5)
        for name, target, kind in _uwp_apps():
            _add(seen, seen, name, target, kind, 6)
    else:
        for path in glob.glob("/Applications/*.app") + glob.glob("/System/Applications/*.app"):
            _add(seen, seen, os.path.basename(path)[:-4], path, "app", 1)
        for path in glob.glob(os.path.expanduser("~/Applications/*.app")):
            _add(seen, seen, os.path.basename(path)[:-4], path, "app", 1)

    apps = sorted(seen.values(), key=lambda a: a["name"].lower())
    with _lock:
        _index["apps"] = apps
        _index["by_name"] = {a["key"]: a for a in apps}
        _index["at"] = time.time()
    return apps


def _score(query_key, app_key, priority=6):
    """
    Higher is better. Text match is only part of it: we also nudge toward tight
    names and curated sources, so "chrome" picks Google Chrome (Start Menu .lnk)
    over an unrelated codex-chrome-native-host.exe on PATH.
    """
    if not query_key or not app_key:
        return 0

    if query_key == app_key:
        base = 100
    elif app_key.startswith(query_key) or query_key.startswith(app_key):
        base = 82
    elif query_key in app_key:
        base = 66
    elif len(app_key) >= 4 and app_key in query_key:
        # reverse containment needs a length guard, otherwise 2-letter helper
        # exes like "rm" match any phrase containing them
        base = 58
    else:
        q_tokens = query_key.split()
        if q_tokens and all(t in app_key.split() or t in app_key for t in q_tokens):
            base = 54
        elif (len(query_key) >= 4
              and abs(len(app_key) - len(query_key)) <= 3
              and app_key[:1] == query_key[:1]
              and difflib.get_close_matches(query_key, [app_key], n=1, cutoff=0.78)):
            # same first letter + similar length: "spotify" must never match
            # "mpnotify" just because difflib finds their letters familiar
            base = 34
        else:
            base = 0
            for token in q_tokens:
                if (len(token) >= 4 and app_key[:1] == token[:1]
                        and abs(len(app_key) - len(token)) <= 3
                        and difflib.get_close_matches(token, [app_key], n=1, cutoff=0.85)):
                    base = 30
                    break

    if not base:
        return 0

    # PATH / Program Files helper binaries are almost never what a person means
    if priority >= 4 and base >= 60 and len(app_key) > len(query_key) + 4:
        base -= 15

    # whole-word hit beats a substring buried inside a longer name
    if query_key in app_key.split():
        base += 8
    # a name close in length to what was said is almost certainly the right one
    if len(app_key) <= len(query_key) + 3:
        base += 4
    # Start Menu / Desktop naming beats PATH and Program Files hunting
    base += max(0, 6 - priority)
    return base


def search(query, limit=5):
    """Best matches for a spoken name: [(score, app), ...] best first."""
    query_key = _norm(query)
    if not query_key:
        return []
    apps = collect()
    ranked = []
    for app in apps:
        score = _score(query_key, app["key"], app.get("priority", 6))
        if score:
            ranked.append((score, app))
    ranked.sort(key=lambda pair: (-pair[0], len(pair[1]["key"]), pair[1]["name"].lower()))
    return ranked[:limit]


def resolve(query):
    results = search(query, limit=1)
    return results[0][1] if results else None


def launch(app):
    """Start a resolved app. Returns True when the OS accepted it."""
    if not app:
        return False
    target, kind = app["target"], app["kind"]
    try:
        if kind == "uwp":
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target])
            return True
        if IS_WIN:
            os.startfile(target)
            return True
        subprocess.Popen(["open", target])
        return True
    except Exception:
        try:
            if kind == "uwp":
                return False
            return shutil.which(target) is not None and bool(subprocess.Popen([target]))
        except Exception:
            return False


def count():
    return len(collect())


def summary(limit=30):
    """Spoken/listable overview used by the 'list my apps' command."""
    apps = collect()
    names = [a["name"] for a in apps]
    head = (f"I can open {len(names)} applications on this computer. "
            f"Among them: " + ", ".join(names[:limit]))
    if len(names) > limit:
        head += f", and {len(names) - limit} more. Just say open followed by the name."
    return head
