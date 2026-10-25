"""
jarvis_voice.py - Live Speech Recognition & Audio Response Loop
Listens through the microphone, requires the wake word, then speaks back.

Language behaviour (see jarvis_lang.py):
  * it LISTENS in the user's language (auto-detected, or ~/.openjarvis/language)
  * the reply TEXT stays English everywhere - HUD, chat log and API
  * only the SPOKEN audio is translated and voiced in the user's language

The wake word is JARVIS. Anything said without it is ignored, so ambient
conversation never reaches the brain.

Conversation window: once the wake word fires, Jarvis stays open for
FOLLOWUP_SECONDS (default 60, override with JARVIS_FOLLOWUP_SECONDS, 0 = off).
Inside that window follow-up commands need no wake word - "Jarvis, what time
is it?" then simply "and tomorrow?" both work. The window is armed only by a
wake-word command (never extended by follow-ups, so background conversation
can not keep it open) and the HUD shows a live countdown.

Auto-language: every phrase is recognised in whichever Indian language it was
spoken in (all 11 + English, see jarvis_lang.INDIAN_LANGUAGES). Groq's
Whisper detects the language FROM THE AUDIO - Google's free STT transliterates
anything into the requested script, so it can not tell languages apart - and
Google remains the no-key fallback. Jarvis then ANSWERS IN THAT LANGUAGE with
the matching neural voice, while the reply text in the HUD, chat log and API
stays English as always.
"""

import audioop
import difflib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import speech_recognition as sr

import jarvis_brain
import jarvis_lang
import jarvis_state

WAKE_WORD = "jarvis"


def _window_seconds():
    """How long the mic stays wake-word-free after a wake-word command."""
    try:
        return max(0, int(os.environ.get("JARVIS_FOLLOWUP_SECONDS", "60")))
    except ValueError:
        return 60


FOLLOWUP_SECONDS = _window_seconds()

# Spellings the recogniser commonly produces for "Jarvis" across languages.
_WAKE_ALIASES = {
    "jarvis", "jervis", "jarwis", "jarvus", "jarviss", "jarviz", "jarbis",
    "jarvice", "jarvisse", "jawris", "charvis",
    # Devanagari, Tamil, Telugu, Arabic
    "जार्विस", "जारविस", "जॉर्विस", "जर्विस",
    "ஜார்விஸ்", "ஜார்விஸ",
    "జార్విస్", "జార్విస",
    "جارفيس", "جارفيز",
}
_VOWELS = set("aeiouy")
_TOKEN_RE = re.compile(
    r"[a-z]+|[ऀ-ॿ]+|[஀-௿]+|[ఀ-ౄ]+|[؀-ۿ]+|[一-鿿]+"
)


def _print(message):
    """Printing must never take down the loop: cp1252 consoles choke on Unicode."""
    try:
        print(message, flush=True)
    except Exception:
        try:
            print(message.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


def _force_utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Wake word
# --------------------------------------------------------------------------- #
def _is_wake_token(token):
    token = token.strip(" ,.!?;:'\"()[]").lower()
    if not token:
        return False
    if token in _WAKE_ALIASES:
        return True
    if not token.isascii() or len(token) < 4:
        return False
    if difflib.SequenceMatcher(None, token, WAKE_WORD).ratio() >= 0.72:
        return True
    # consonant skeleton: "jrvs" covers jarvis/jarvs/jervis noise
    skeleton = "".join(ch for ch in token if ch not in _VOWELS)
    return skeleton in {"jrvs", "jrvs", "jrvss"} and len(token) <= 9


def wake_filter(transcript):
    """
    (matched, text_without_wake_word).
    matched=False  -> the phrase was not addressed to Jarvis; ignore it.
    """
    if not transcript:
        return False, ""
    tokens = re.findall(r"\S+", transcript)
    matched = False
    kept = []
    for token in tokens:
        bare = token.strip(" ,.!?;:'\"()[]")
        if _is_wake_token(bare):
            matched = True
        else:
            kept.append(token)
    if not matched:
        return False, ""
    return True, " ".join(kept).strip(" ,.!?;:'\"")


def _gate(query, matched, rest):
    """
    (accept, text_for_brain, via_followup) - the single decision point for the
    mic loop. Wake word always wins; inside the conversation window a phrase
    without the wake word is accepted as a follow-up, verbatim.
    """
    if matched:
        return True, rest, False
    if query and jarvis_state.followup_active():
        return True, query.strip(), True
    return False, "", False


def _recognise(recognizer, audio, language):
    """
    (text, detected_language). Primary: one Groq Whisper call whose language
    field is detected from the audio itself - correct for every Indian
    language, even romanised speech. Fallback (no key / Whisper down): Google
    STT with the active language, then the language probe, where the
    transcript's script decides. Raises sr.UnknownValueError on silence.
    """
    # AudioData has no get_duration() in SpeechRecognition 3.17 - compute it
    # from the raw bytes so the short-clip trust rule gets a real number.
    try:
        duration = len(audio.raw_data) / float(audio.sample_width * audio.sample_rate)
    except Exception:
        duration = None
    try:
        heard = jarvis_lang.groq_transcribe(audio.get_wav_data(), duration=duration)
    except Exception as exc:
        _print(f"[STT] whisper unavailable ({exc}) - using Google")
        heard = None
    if heard and heard[0]:
        return heard

    try:
        text = recognizer.recognize_google(audio, language=language).strip()
    except sr.UnknownValueError:
        text = ""
    if text:
        return text, jarvis_lang.detect_script(text, current=language) or language

    for candidate in jarvis_lang.candidate_languages(language):
        if candidate.lower() == (language or "").lower():
            continue
        try:
            text = recognizer.recognize_google(audio, language=candidate).strip()
        except sr.UnknownValueError:
            continue
        if not text:
            continue
        return text, jarvis_lang.detect_script(text, current=candidate) or candidate

    raise sr.UnknownValueError()


# --------------------------------------------------------------------------- #
# Speech output
# --------------------------------------------------------------------------- #
def _speak_system(text):
    """Native OS voice: macOS `say`, Windows pyttsx3/SAPI (offline)."""
    if sys.platform == "darwin":
        subprocess.run(["say", "-v", jarvis_lang.user_language().split("-")[0], text])
        return
    import pyttsx3
    engine = pyttsx3.init()
    voice = jarvis_lang.pick_voice(engine)
    if voice:
        engine.setProperty("voice", voice)
    engine.say(text)
    engine.runAndWait()


SPEECH_LIMIT = 420


def _for_speech(text):
    """
    Voice has a budget the HUD does not. Long answers are spoken up to a
    sentence boundary, then pointed at the chat log - so a 600-word reply never
    leaves the microphone blocked for minutes. The TEXT itself is untouched.
    """
    if len(text) <= SPEECH_LIMIT:
        return text
    cut = text[:SPEECH_LIMIT]
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("\n"))
    if boundary >= 120:
        cut = cut[:boundary + 1]
    else:
        cut = cut.rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return cut + " The full answer is in the chat log."


# One speaker at a time: mic replies, API replies and timer alarms share it.
_speak_lock = threading.Lock()


def speak(text):
    """
    Speaks `text`. The string printed to the HUD/log is the original English;
    only the audio changes language. Thread-safe: the mic loop, the HTTP API
    and timer threads can all ask for speech without overlapping audio.
    """
    if not text:
        return
    with _speak_lock:
        _speak_locked(text)


def _speak_locked(text):
    jarvis_state.set_status("SPEAKING")
    _print(f"\n[JARVIS]: {text}\n")

    lang = jarvis_lang.user_language()
    try:
        text = _for_speech(text)
        if jarvis_lang.is_english(lang):
            _speak_system(text)
            return

        if not jarvis_lang.supports_neural_speech(lang):
            # No voice exists for this language in any keyless catalogue
            # (Punjabi, Odia). Never read foreign text with an English voice -
            # say the English original instead.
            _print(f"[TTS] no voice exists for {lang}, speaking English")
            _speak_system(text)
            return

        spoken = jarvis_lang.spoken_text(text, lang)
        handle, tmp_path = tempfile.mkstemp(prefix="jarvis_speak_", suffix=".mp3")
        os.close(handle)
        os.remove(tmp_path)
        if jarvis_lang.synthesize_neural(spoken, lang, tmp_path) and \
                jarvis_lang.play_audio_file(tmp_path):
            return

        # No usable voice for this language: never read foreign text with an
        # English voice - fall back to the original English sentence.
        _print(f"[TTS] no usable voice for {lang}, speaking English instead")
        _speak_system(text)
    except Exception as exc:
        _print(f"[TTS unavailable]: {exc}")
    finally:
        jarvis_state.set_status("LISTENING")
        jarvis_state.set_level(0.0)


# --------------------------------------------------------------------------- #
# Query handling
# --------------------------------------------------------------------------- #
def _energy(audio):
    """Normalise a captured phrase to 0..1 for the HUD oscilloscope."""
    try:
        rms = audioop.rms(audio.get_raw_data(), audio.sample_width)
        return max(0.0, min(1.0, rms / 1400.0))
    except Exception:
        return 0.0


def handle_query(query):
    """Run one recognised phrase through the brain and speak the answer."""
    jarvis_state.set_status("PROCESSING")
    jarvis_state.add_message("you", query)

    _cleaned_matched, q_clean = wake_filter(query)
    if not _cleaned_matched:
        # typed/API input needs no wake word, but strip one if the user added it
        lowered = query.lower()
        if "jarvis" in lowered:
            q_clean = lowered.replace("jarvis", "").strip(" ,.!?")
        else:
            q_clean = query.strip()
    else:
        q_clean = q_clean.lower().strip()

    if not q_clean:
        jarvis_state.add_message("jarvis", "Yes sir, I'm listening.")
        speak("Yes sir, I'm listening.")
        return "Yes sir, I'm listening."

    with jarvis_state.brain_lock:
        response = jarvis_brain.think(q_clean)
    response = (response or "").strip() or (
        "I don't have an answer for that right now. Try asking again in a moment.")
    jarvis_state.add_message("jarvis", response)
    speak(response)
    return response


def _text_console():
    """Fallback when no microphone is available."""
    print("Falling back to text console input mode.")
    jarvis_state.set_status("LISTENING")
    while True:
        try:
            user_input = input("You: ")
            if user_input.strip():
                handle_query(user_input.strip())
        except (KeyboardInterrupt, EOFError):
            break


def listen_loop():
    _force_utf8_console()
    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True
    recognizer.pause_threshold = 0.8
    language = jarvis_lang.stt_language()
    neural = jarvis_lang.supports_neural_speech() and not jarvis_lang.is_english()

    try:
        mic = sr.Microphone()
    except Exception as e:
        print(f"Error accessing microphone: {e}")
        _text_console()
        return

    with mic as source:
        print("\n" + "=" * 50)
        print("  JARVIS AI ASSISTANT IS ACTIVE AND LISTENING")
        print(f"  Wake word : {WAKE_WORD.upper()}")
        if FOLLOWUP_SECONDS:
            print(f"  Follow-up : {FOLLOWUP_SECONDS}s open after '{WAKE_WORD}'"
                  " - keep talking, no wake word needed")
        else:
            print("  Follow-up : off (JARVIS_FOLLOWUP_SECONDS=0)")
        print(f"  Language  : {language} ({jarvis_lang.describe()})")
        print("  Languages : auto-detects Hindi, Telugu, Tamil, Kannada, Malayalam,")
        print("              Bengali, Marathi, Gujarati, Punjabi, Odia, Urdu + English")
        print("  Say commands like:")
        print(f"    - '{WAKE_WORD}, open VS Code'")
        print(f"    - '{WAKE_WORD}, what's the weather?'")
        print(f"    - '{WAKE_WORD}, list my apps'")
        print(f"    - '{WAKE_WORD}, stock market update'")
        print("=" * 50 + "\n")
        recognizer.adjust_for_ambient_noise(source, duration=1.0)

    jarvis_state.set_status("LISTENING")

    while True:
        try:
            with mic as source:
                print("Listening... ", end="", flush=True)
                audio = recognizer.listen(source, timeout=10, phrase_time_limit=8)
            jarvis_state.set_level(_energy(audio))

            print("Processing... ", end="", flush=True)
            query, detected = _recognise(recognizer, audio, language)
            print(f"\nYou said: \"{query}\"")

            if jarvis_lang.adopt_detection(detected):
                language = jarvis_lang.stt_language()
                print(f"  [LANG] {jarvis_lang.language_name(language)} detected - answering in it")

            matched, rest = wake_filter(query)

            if not matched and query and not jarvis_lang.is_english():
                # The recogniser was aimed at the user's language; if it missed
                # the Latin wake word, retry once against an English model.
                try:
                    alt = recognizer.recognize_google(audio, language="en-US").strip()
                    alt_matched, alt_rest = wake_filter(alt)
                    if alt_matched:
                        matched, rest, query = True, alt_rest, alt
                except sr.UnknownValueError:
                    pass

            accept, command, via_followup = _gate(query, matched, rest)
            if not accept:
                left = int(round(jarvis_state.followup_remaining()))
                print(f"  (no wake word - ignored{' | window closes in %ds' % left if left else ''})")
                jarvis_state.set_status("LISTENING")
                jarvis_state.set_level(0.0)
                continue

            if via_followup:
                print("  (follow-up window - no wake word needed)")
            else:
                jarvis_state.note_wake()
                print("  (wake word detected - listening)")

            handle_query(command)
            # Only a wake-word command arms the window; follow-ups never
            # extend it, so ambient talk cannot keep Jarvis open forever.
            if not via_followup and FOLLOWUP_SECONDS:
                jarvis_state.arm_followup(FOLLOWUP_SECONDS)
            jarvis_state.set_status("LISTENING")
            jarvis_state.set_level(0.0)

        except sr.WaitTimeoutError:
            jarvis_state.set_level(0.0)
            continue
        except sr.UnknownValueError:
            jarvis_state.set_level(0.0)
            continue
        except KeyboardInterrupt:
            print("\nShutting down Jarvis. Goodbye!")
            jarvis_state.set_status("OFFLINE")
            break
        except Exception as e:
            print(f"\n[Notice]: {e}")
            jarvis_state.set_status("LISTENING")
            time.sleep(0.5)


if __name__ == "__main__":
    listen_loop()
