"""
jarvis_lang.py - Which language to listen in, and which voice to speak with.

The reply TEXT always stays English (the HUD, the chat log, the API). Only the
spoken audio is localised: `spoken_text()` translates for speech, and the
backend used to say it is chosen by `voice_for()`.

Language resolution order:
  1. ~/.openjarvis/language   -> one BCP-47 tag, e.g. hi-IN, te-IN, fr-FR
  2. the Windows UI culture   -> automatic, no config needed
  3. the POSIX locale, then en-IN
"""

import locale
import os
import re
import subprocess

LANGUAGE_FILE = os.path.expanduser("~/.openjarvis/language")

# --------------------------------------------------------------------------- #
# All Indian languages the assistant listens for and can answer in.
# Probe order matters: the frequent ones come first so auto-detection stays
# fast. en-IN is last - Latin text is also what an English reply produces.
# --------------------------------------------------------------------------- #
INDIAN_LANGUAGES = [
    "hi-IN",  # Hindi      Devanagari
    "te-IN",  # Telugu     Telugu script
    "ta-IN",  # Tamil      Tamil script
    "kn-IN",  # Kannada    Kannada script
    "ml-IN",  # Malayalam  Malayalam script
    "bn-IN",  # Bengali    Bengali script
    "mr-IN",  # Marathi    Devanagari
    "gu-IN",  # Gujarati   Gujarati script
    "pa-IN",  # Punjabi    Gurmukhi script
    "or-IN",  # Odia       Oriya script
    "ur-IN",  # Urdu       Arabic script
    "en-IN",  # English    Latin
]

LANG_NAMES = {
    "en-IN": "English", "hi-IN": "Hindi", "te-IN": "Telugu",
    "ta-IN": "Tamil", "kn-IN": "Kannada", "ml-IN": "Malayalam",
    "bn-IN": "Bengali", "mr-IN": "Marathi", "gu-IN": "Gujarati",
    "pa-IN": "Punjabi", "or-IN": "Odia", "ur-IN": "Urdu",
}

# Unicode blocks (lo, hi) of the Indian scripts. Exact and non-overlapping,
# so the writing system alone tells us what language was spoken.
_SCRIPT_BLOCKS = [
    (0x0900, 0x097F, "hi-IN"),   # Devanagari  -> Hindi / Marathi
    (0x0980, 0x09FF, "bn-IN"),   # Bengali
    (0x0A00, 0x0A7F, "pa-IN"),   # Gurmukhi    -> Punjabi
    (0x0A80, 0x0AFF, "gu-IN"),   # Gujarati
    (0x0B00, 0x0B7F, "or-IN"),   # Oriya
    (0x0B80, 0x0BFF, "ta-IN"),   # Tamil
    (0x0C00, 0x0C7F, "te-IN"),   # Telugu
    (0x0C80, 0x0CFF, "kn-IN"),   # Kannada
    (0x0D00, 0x0D7F, "ml-IN"),   # Malayalam
    (0x0600, 0x06FF, "ur-IN"),   # Arabic      -> Urdu
    (0x0041, 0x024F, "en-IN"),   # Latin
]


def detect_script(text, current=None):
    """
    What language is this transcript written in?
      'te-IN' / 'ta-IN' / ...  -> that language (script is ground truth)
      'en-IN'                  -> Latin letters (English / romanised)
      None                     -> digits/punctuation only: no signal.
    Devanagari belongs to Hindi and Marathi; the active language breaks the tie.
    """
    if not text:
        return None
    counts = {}
    for ch in text:
        cp = ord(ch)
        for lo, hi, lang in _SCRIPT_BLOCKS:
            if lo <= cp <= hi:
                counts[lang] = counts.get(lang, 0) + 1
                break
    if not counts:
        return None
    best = max(counts, key=counts.get)
    if best == "hi-IN" and (current or user_language()).lower().startswith("mr"):
        return "mr-IN"
    return best


def candidate_languages(current=None):
    """Probe order for auto-detection: the active language first, then all."""
    current = (current or user_language()).lower()
    ordered = [lang for lang in INDIAN_LANGUAGES if lang.lower() == current]
    ordered += [lang for lang in INDIAN_LANGUAGES if lang.lower() != current]
    return ordered


# --------------------------------------------------------------------------- #
# Primary speech-to-text: Groq's Whisper, one call, language auto-detected.
#
# Google's free web STT transliterates ANY speech into whatever script you
# ask for (English heard as te-IN comes back as Telugu script), so per-language
# probing can not tell languages apart. Whisper listens to the AUDIO itself,
# so it names the language correctly even for romanised speech - and it
# supports every Indian language listed above. Google stays as the offline /
# no-key fallback in jarvis_voice._recognise.
# --------------------------------------------------------------------------- #
WHISPER_MODELS = ("whisper-large-v3", "whisper-large-v3-turbo")

# Whisper can confidently hallucinate on fleeting clips (a 1.3s Tamil phrase
# came back as perfect-looking English). Native-script claims are always
# trusted, but an "English" claim on a clip shorter than this never CHANGES
# the language - it can only keep what is already active. The hallucinated
# text itself is harmless: it carries no wake word, so the mic loop drops it.
MIN_ENGLISH_SECONDS = 2.0

_LANG_TAGS = {
    "hi": "hi-IN", "te": "te-IN", "ta": "ta-IN", "kn": "kn-IN",
    "ml": "ml-IN", "bn": "bn-IN", "mr": "mr-IN", "gu": "gu-IN",
    "pa": "pa-IN", "or": "or-IN", "ur": "ur-IN", "en": "en-IN",
}

# Groq answers with the language NAME ("Telugu"), not an ISO code.
_LANG_NAMES = {
    "english": "en-IN", "hindi": "hi-IN", "telugu": "te-IN",
    "tamil": "ta-IN", "kannada": "kn-IN", "malayalam": "ml-IN",
    "bengali": "bn-IN", "marathi": "mr-IN", "gujarati": "gu-IN",
    "punjabi": "pa-IN", "odia": "or-IN", "oriya": "or-IN",
    "urdu": "ur-IN",
}


def normalize_lang(code):
    """Whisper's language (name like 'Telugu' or code like 'te') -> BCP-47
    tag we can act on, else None (unknown / unsupported language)."""
    if not code:
        return None
    key = str(code).strip().lower().replace("_", "-")
    if key in _LANG_NAMES:
        return _LANG_NAMES[key]
    return _LANG_TAGS.get(key.split("-")[0])


def groq_transcribe(wav_bytes, duration=None, filename="clip.wav"):
    """
    (text, language) from one Whisper call with the language auto-detected.
    Returns None when there is no API key or the call fails - the caller then
    falls back to Google STT, so a network/key problem never kills the loop.
    """
    client = _translator()
    if client is None or not wav_bytes:
        return None
    import tempfile
    handle, path = tempfile.mkstemp(suffix=".wav")
    try:
        os.write(handle, wav_bytes)
        os.close(handle)
        handle = None
        for model in WHISPER_MODELS:
            for fmt in ("verbose_json", "json"):
                try:
                    with open(path, "rb") as fh:
                        resp = client.audio.transcriptions.create(
                            model=model, file=fh, response_format=fmt)
                    text = (getattr(resp, "text", "") or "").strip()
                    lang = getattr(resp, "language", None)
                    if not text and isinstance(resp, dict):
                        text = (resp.get("text") or "").strip()
                        lang = lang or resp.get("language")
                    if not text:
                        continue          # empty - try the next model/format
                    detected = normalize_lang(lang)
                    if duration is None:
                        duration = getattr(resp, "duration", None)
                    if detected and detected != "en-IN" and \
                            detect_script(text) == "en-IN":
                        # Claimed a native language but wrote ENGLISH: the
                        # language ID is unbacked (hallucination), so never
                        # switch on it - keep the active language. The Latin
                        # text itself carries no wake word and is dropped.
                        detected = user_language()
                    elif detected == "en-IN" and duration is not None \
                            and duration < MIN_ENGLISH_SECONDS:
                        # Fleeting clip claiming English: keep the active
                        # language instead of downgrading (see the note above).
                        detected = user_language()
                    return text, detected or user_language()
                except Exception:
                    continue              # rate limit or model unavailable
        return None
    except Exception:
        return None
    finally:
        if handle is not None:
            try:
                os.close(handle)
            except OSError:
                pass
        try:
            os.remove(path)
        except OSError:
            pass

# edge-tts voices we prefer per language (name, voice-id); fallback is auto lookup
_cache = {"lang": None, "edge_voices": None, "client": None, "explicit": False}
_translate_cache = {}


def user_language():
    """BCP-47 tag for this user, resolved once and cached."""
    if _cache["lang"]:
        return _cache["lang"]

    try:
        if os.path.exists(LANGUAGE_FILE):
            with open(LANGUAGE_FILE, encoding="utf-8") as handle:
                raw = handle.read().strip()
            if raw and not raw.startswith("#"):
                _cache["lang"] = raw.split(",")[0].strip()
                return _cache["lang"]
    except Exception:
        pass

    if os.name == "nt":
        # Get-Culture reflects how the user actually talks (en-IN on this box);
        # CurrentUICulture is only the display language and is often plain en-US.
        for expr in ("(Get-Culture).Name",
                     "[System.Globalization.CultureInfo]::CurrentUICulture.Name"):
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", expr],
                    capture_output=True, text=True, timeout=8,
                    creationflags=0x08000000,
                )
                name = (out.stdout or "").strip()
                if re.fullmatch(r"[a-zA-Z]{2,3}(-[A-Za-z0-9]+)*", name or ""):
                    _cache["lang"] = name
                    return name
            except Exception:
                continue

    try:
        loc = locale.getlocale()[0]
        if loc and "_" in loc:
            _cache["lang"] = loc.replace("_", "-")
            return _cache["lang"]
    except Exception:
        pass

    _cache["lang"] = "en-IN"
    return "en-IN"


def set_language(lang, explicit=True):
    """Choose the conversation language. explicit=True comes from a command
    ("speak tamil"); explicit=False comes from auto-detection."""
    if not lang:
        return user_language()
    _cache["lang"] = lang
    if explicit:
        _cache["explicit"] = True
    return lang


def adopt_detection(detected):
    """
    Mirror whatever language the user just spoke. Returns True on change.
    A detected non-English language always wins (they said Telugu, answer in
    Telugu). English never downgrades an explicit "speak X" command - a
    misdetected English phrase must not cancel what they asked for.
    """
    if not detected or not detected.lower().endswith("-in"):
        return False
    current = (user_language() or "").lower()
    if detected.lower() == current:
        return False
    if detected.lower().startswith("en") and _cache.get("explicit"):
        return False
    _cache["lang"] = detected
    return True


def language_name(lang=None):
    return LANG_NAMES.get((lang or user_language()).lower(), lang or user_language())


def reset_language_cache():
    _cache["lang"] = None
    _cache["explicit"] = False


def is_english(lang=None):
    return (lang or user_language()).lower().split("-")[0] == "en"


def stt_language(lang=None):
    """Language code for Google speech recognition (BCP-47)."""
    return lang or user_language()


# --------------------------------------------------------------------------- #
# Translation (voice only - text replies stay English)
# --------------------------------------------------------------------------- #
TRANSLATE_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"]


def _translator():
    if _cache["client"] is not None:
        return _cache["client"]
    try:
        import jarvis_brain
        key = jarvis_brain._get_api_key()
        if not key:
            return None
        from openai import OpenAI
        _cache["client"] = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        return _cache["client"]
    except Exception:
        return None


def spoken_text(text, lang=None):
    """
    Translate `text` for speech in `lang`. The English string itself is never
    touched elsewhere - this is the only place language changes anything.
    Returns the original text whenever translation is unnecessary or fails.
    """
    if not text:
        return text
    lang = lang or user_language()
    if is_english(lang):
        return text

    key = (lang, text)
    if key in _translate_cache:
        return _translate_cache[key]

    client = _translator()
    if client is None:
        return text

    prompt = (
        f"Translate the assistant's reply into {language_name(lang)} ({lang}). "
        "Keep product names, app names, proper nouns, URLs and numbers exactly as they are. "
        "Reply with ONLY the translation, no quotes, no labels, no explanations."
    )
    answer = ""
    for model in TRANSLATE_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": text}],
                max_tokens=700, temperature=0.2,
            )
            answer = (resp.choices[0].message.content or "").strip()
            if answer:
                break
        except Exception:
            continue

    if not answer:
        return text
    if len(_translate_cache) > 400:
        _translate_cache.clear()
    _translate_cache[key] = answer
    return answer


def to_english(text):
    """
    Best-effort translation of a NON-English user command into English, so the
    local intent matchers (weather, time, open app, ...) can act on it with
    real local data instead of a generic LLM answer. Returns None when it
    cannot translate - callers simply skip the shortcut.
    """
    if not text:
        return None
    try:
        if text.isascii():
            return text
    except AttributeError:
        return None
    key = ("en", text)
    if key in _translate_cache:
        return _translate_cache[key]
    client = _translator()
    if client is None:
        return None
    prompt = (
        "Translate this user's voice command into English. Keep app names, "
        "brand names, numbers and URLs exactly as they are. "
        "Reply with ONLY the translation."
    )
    for model in TRANSLATE_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": prompt},
                          {"role": "user", "content": text}],
                max_tokens=200, temperature=0.0,
            )
            answer = (resp.choices[0].message.content or "").strip()
            if answer:
                if len(_translate_cache) > 400:
                    _translate_cache.clear()
                _translate_cache[key] = answer
                return answer
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- #
# Voices
# --------------------------------------------------------------------------- #
def pick_voice(engine, lang=None):
    """A local SAPI voice id for `lang`, or None when the OS has no such voice."""
    lang = lang or user_language()
    try:
        voices = engine.getProperty("voices") or []
    except Exception:
        return None

    blobs = []
    for voice in voices:
        parts = [str(getattr(voice, "id", "")), str(getattr(voice, "name", ""))]
        try:
            parts.append(str(getattr(voice, "languages", "") or ""))
        except Exception:
            pass
        blobs.append((voice, " ".join(parts).lower()))

    base = lang.split("-")[0].lower()
    candidates = [lang.replace("-", "_").lower(), lang.lower(), base]
    for token in candidates:
        for voice, blob in blobs:
            if token in blob:
                return voice.id
    pattern = re.compile(rf"(^|[^a-z]){re.escape(base)}([^a-z]|$)")
    for voice, blob in blobs:
        if pattern.search(blob):
            return voice.id
    return None


def _edge_voices():
    if _cache["edge_voices"] is not None:
        return _cache["edge_voices"]
    try:
        import asyncio
        import edge_tts
        voices = asyncio.run(edge_tts.list_voices())
    except Exception:
        voices = []
    _cache["edge_voices"] = voices or []
    return _cache["edge_voices"]


def edge_voice_for(lang=None):
    """Best neural voice id for `lang` from the keyless edge-tts catalogue."""
    lang = (lang or user_language()).lower()
    voices = _edge_voices()
    if not voices:
        return None
    exact = [v for v in voices if v.get("Locale", "").lower() == lang]
    if not exact:
        exact = [v for v in voices
                 if v.get("Locale", "").lower().split("-")[0] == lang.split("-")[0]
                 and v.get("Locale", "").lower().startswith(lang.split("-")[0] + "-in")]
    if not exact:
        exact = [v for v in voices
                 if v.get("Locale", "").lower().split("-")[0] == lang.split("-")[0]]
    if not exact:
        return None
    for preferred in ("Madhur", "Swara", "Hemant", "Neerja", "Prabhat", "Valluvar",
                      "Pallavi", "Sarai", "Anand", "Arun", "Shakira", "Aria"):
        for voice in exact:
            short = voice.get("ShortName", "")
            if preferred.lower() in short.lower():
                return short
    return exact[0].get("ShortName")


def supports_neural_speech(lang=None):
    return edge_voice_for(lang) is not None


def synthesize_neural(text, lang, out_path, timeout=30):
    """Write `text` spoken in `lang` to out_path (mp3). Returns True on success."""
    voice = edge_voice_for(lang)
    if not voice or not text:
        return False
    try:
        import asyncio
        import edge_tts

        async def _run():
            tts = edge_tts.Communicate(text, voice)
            await tts.save(out_path)

        asyncio.run(asyncio.wait_for(_run(), timeout=timeout))
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception:
        return False


def play_audio_file(path, timeout=None):
    """Play an mp3 silently through Windows' MediaPlayer and wait for it."""
    if os.name != "nt" or not os.path.exists(path):
        return False
    script = (
        "Add-Type -AssemblyName PresentationCore;"
        "$p = New-Object System.Windows.Media.MediaPlayer;"
        f"$p.Open([Uri]'{path}');"
        "$sw=[Diagnostics.Stopwatch]::StartNew();"
        "while(-not $p.NaturalDuration.HasTimeSpan -and $sw.ElapsedMilliseconds -lt 4000)"
        "{Start-Sleep -Milliseconds 100};"
        "$p.Play();"
        "$ms = 8000;"
        "if($p.NaturalDuration.HasTimeSpan){$ms = [int]($p.NaturalDuration.TimeSpan.TotalMilliseconds + 400)};"
        "Start-Sleep -Milliseconds $ms; $p.Close()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=timeout or 45,
                       creationflags=0x08000000)
        return True
    except Exception:
        return False
    finally:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def describe():
    lang = user_language()
    return (f"language={lang} english={is_english(lang)} "
            f"sapi={bool(_cache.get('lang'))} edge_voice={edge_voice_for(lang)}")
