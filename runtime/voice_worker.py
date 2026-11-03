"""Full Conversational Voice Engine for Apple Silicon M4 / Antigravity Personal Agent.
Features:
- Speech Input: Connect IT ClipMic Pro (USBAudio1.0) or default input via sounddevice
- Speech Recognition: mlx-whisper (whisper-large-v3-turbo, Apple Silicon M4)
- Intelligence: Antigravity Agent LLM Engine / LM Studio (nvidia-nemotron-3.5-bolt-e4b-mlx)
- Voice Output: macOS native Zuzana (Premium)
- Features: Real-time streaming preview, 4s pause / instant microphone button cut-off submission,
            multi-turn conversation memory, echo-loop protection, JSON event stream for Web UI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import numpy as np

# Ensure ffmpeg in PATH for mlx-whisper
try:
    import imageio_ffmpeg
    ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ["PATH"] = f"{ffmpeg_dir}:{os.environ.get('PATH', '')}"
except Exception:
    pass

try:
    import mlx_whisper
except ImportError:
    mlx_whisper = None

try:
    import sounddevice as sd
except ImportError:
    sd = None

# Audio settings
SAMPLE_RATE = 16000
BLOCK_SIZE = 1600  # 100ms

# VAD & Timing Thresholds
SPEECH_START_THRESHOLD = 0.010       # RMS to start recording
SPEECH_CONTINUE_THRESHOLD = 0.0030    # RMS to keep speech alive
SILENCE_TIMEOUT = 4.0               # Continuous seconds of silence before auto-finish
DEBOUNCE_ZERO_CHUNKS = 6             # 6 blocks = 0.6s of confirmed digital zero for button press
PEAK_MIN_THRESHOLD = 0.035          # Peak amplitude needed during speech
MIN_SPEECH_DURATION = 0.6           # Minimum speech duration to process
PARTIAL_INTERVAL = 0.7              # Interval for live streaming updates

# TTS State & Interruption Controls
is_tts_playing = False
tts_cooldown_until = 0.0
active_voice = "Zuzana (Premium)"
_tts_lock = threading.Lock()
_current_tts_proc: subprocess.Popen | None = None
_tts_interrupted = False

# LM Studio API Settings (Fallback / Standalone)
LMSTUDIO_URL = "http://localhost:1234/v1/chat/completions"
LLM_MODEL = "nvidia-nemotron-3.5-bolt-e4b-mlx"

SYSTEM_PROMPT_CS = (
    "Jsi inteligentní, přirozená a pohotová česká hlasová asistentka. "
    "Mluvíš česky. Tvé odpovědi jsou určeny pro předčítání hlasem, proto buď stručná, "
    "mluv k věci a srozumitelně, typicky v rozsahu 1 až 3 vět. "
    "Nikdy nepoužívej formátování markdown (žádné hvězdičky, odrážky, mřížky), "
    "ani emotikony, aby text zněl naprosto přirozeně při vyslovení."
)

SYSTEM_PROMPT_EN = (
    "You are an intelligent, natural, and helpful voice assistant. "
    "You speak English. Your responses are spoken aloud, so be concise, "
    "natural and direct, typically 1 to 3 sentences. "
    "Never use markdown formatting (no asterisks, bullet points, hashes) "
    "or emojis, so the text sounds completely natural when spoken."
)

HALLUCINATIONS = [
    # Czech
    "titulky", "johnyx", "sledování", "překlad", "časování",
    "konec", "odběr", "zvoneček", "byl jsem rád", "děkuji za zhlédnutí",
    # English
    "subtitles by", "thank you for watching", "thanks for watching",
    "subscribe to my channel", "please subscribe", "like and subscribe",
    "transcription by", "copyright by", "all rights reserved",
]

is_tts_playing = False
tts_cooldown_until = 0.0
stop_requested = False


def emit_event(event_type: str, data: dict | None = None) -> None:
    """Emit structured JSON event on stdout for agent controller while keeping human logs clean."""
    payload = {"event": event_type, "timestamp": time.time(), "data": data or {}}
    sys.stdout.write(f"\n__VOICE_EVENT__{json.dumps(payload, ensure_ascii=False)}\n")
    sys.stdout.flush()


def log(msg: str, end: str = "\n", flush: bool = True) -> None:
    sys.stderr.write(msg + end)
    if flush:
        sys.stderr.flush()


def clean_text_for_speech(text: str) -> str:
    """Removes markdown syntax, emojis, and symbols that sound weird when spoken."""
    text = re.sub(r'[*_#`~>\[\]\(\)]', '', text)
    text = re.sub(r'[^\w\s.,!?:;\-\'\"]', '', text, flags=re.UNICODE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def stop_tts() -> None:
    """Immediately halts any active speech synthesizer process."""
    global is_tts_playing, tts_cooldown_until, _current_tts_proc, _tts_interrupted
    with _tts_lock:
        _tts_interrupted = True
        if _current_tts_proc and _current_tts_proc.poll() is None:
            try:
                _current_tts_proc.terminate()
                _current_tts_proc.wait(timeout=0.15)
            except Exception:
                try:
                    _current_tts_proc.kill()
                except Exception:
                    pass
        _current_tts_proc = None
        is_tts_playing = False
        tts_cooldown_until = 0.0


def speak(text: str, voice_name: str | None = None) -> None:
    """Speaks text using macOS native speech synthesizer. Supports instant user interruption."""
    global is_tts_playing, tts_cooldown_until, _current_tts_proc, _tts_interrupted, active_voice
    clean_text = clean_text_for_speech(text)
    if not clean_text:
        is_tts_playing = False
        return

    selected_voice = voice_name or active_voice or "Zuzana (Premium)"

    # If any previous TTS is running, stop it first
    stop_tts()

    with _tts_lock:
        _tts_interrupted = False
        is_tts_playing = True

    log(f"\n[{selected_voice.upper()} MLUVÍ]: \"{clean_text}\"")
    emit_event("speaking_start", {"text": clean_text, "voice": selected_voice})
    try:
        cmd = ["say", "-v", selected_voice, clean_text]
        proc = subprocess.Popen(cmd)
        with _tts_lock:
            _current_tts_proc = proc
        proc.wait()
    except Exception as e:
        log(f"[CHYBA PŘI HLASOVÉM VÝSTUPU]: {e}")
    finally:
        with _tts_lock:
            _current_tts_proc = None
            interrupted = _tts_interrupted
            is_tts_playing = False
            if interrupted:
                tts_cooldown_until = 0.0
                emit_event("speaking_interrupted", {"text": clean_text})
            else:
                tts_cooldown_until = time.time() + 0.6
                emit_event("speaking_end", {"cooldown_seconds": 0.6})


def query_llm(messages: list[dict], url: str = LMSTUDIO_URL, model: str = LLM_MODEL) -> str:
    """Queries LM Studio local API with multi-turn conversation history (standalone mode)."""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 150
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["choices"][0]["message"]["content"].strip()


def is_hallucination(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return True
    for h in HALLUCINATIONS:
        if h in low:
            return True
    return False


def listen_commands():
    """Background thread to process stdin commands in managed mode."""
    global stop_requested
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd_data = json.loads(line) if line.startswith("{") else {"action": line}
            action = cmd_data.get("action", "").lower()
            if action in ("stop", "exit", "quit"):
                stop_tts()
                stop_requested = True
                emit_event("stopped", {"reason": "requested"})
                break
            elif action in ("stop_speech", "stop_tts", "interrupt"):
                stop_tts()
            elif action == "speak":
                text = cmd_data.get("text", "")
                vname = cmd_data.get("voice") or active_voice
                if text:
                    speak(text, voice_name=vname)
            elif action == "ping":
                emit_event("pong", {"status": "ok"})
        except Exception as e:
            log(f"[CHYBA PŘÍKAZU STDIN]: {e}")


def main():
    global is_tts_playing, tts_cooldown_until, stop_requested, active_voice

    parser = argparse.ArgumentParser(description="Voice Engine for Antigravity Personal Agent")
    parser.add_argument("--managed", action="store_true", help="Run in agent-managed mode (IPC via stdin/stdout)")
    parser.add_argument("--device", type=str, default=None, help="Audio input device name or index")
    parser.add_argument("--language", choices=["cs", "en"], default="cs", help="Spoken language: cs (Czech) or en (English)")
    parser.add_argument("--voice", type=str, default=None, help="macOS voice name (default Zuzana for cs, Samantha for en)")
    parser.add_argument("--no-greeting", action="store_true", help="Do not speak initial greeting on startup")
    parser.add_argument("--agent-url", type=str, default=None, help="Agent chat HTTP endpoint (e.g. http://localhost:8765/api/chat/send)")
    args = parser.parse_args()

    if not args.voice:
        args.voice = "Samantha" if args.language == "en" else "Zuzana (Premium)"
    active_voice = args.voice

    system_prompt = SYSTEM_PROMPT_EN if args.language == "en" else SYSTEM_PROMPT_CS

    log("=" * 70)
    log("  Mac Mini M4: HLASOVÝ AGENT PRO ANTIGRAVITY AGENT")
    log("=" * 70)
    log(f"• LLM Model: {LLM_MODEL}")
    log(f"• Jazyk: {'English (en)' if args.language == 'en' else 'Čeština (cs)'}")
    log(f"• Hlasový přepis: mlx-whisper (whisper-large-v3-turbo, lang={args.language})")
    log(f"• Hlasový výstup: {args.voice}")
    log(f"• Režim řízení: {'Managed (Antigravity Bridge)' if args.managed else 'Standalone'}")
    log("-" * 70)

    # Start command listener in managed mode
    if args.managed:
        cmd_thread = threading.Thread(target=listen_commands, daemon=True)
        cmd_thread.start()

    if mlx_whisper is None or sd is None:
        log("[CHYBA] mlx_whisper nebo sounddevice není nainstalován v tomto prostředí.")
        emit_event("error", {"error": "Missing mlx_whisper or sounddevice"})
        return

    # Pre-warm Whisper on Apple Silicon
    log("Načítám a předehřívám Whisper na Apple M4...")
    dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mlx_whisper.transcribe(dummy, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language=args.language)
    log("Vše je připraveno!")
    emit_event("ready", {"device": args.device, "voice": args.voice, "language": args.language})

    conversation_history = [
        {"role": "system", "content": system_prompt}
    ]

    if not args.no_greeting:
        greeting = (
            "Hello! I am ready to talk. What would you like to ask?"
            if args.language == "en"
            else "Ahoj! Jsem připravena k povídání. Na co se chceš zeptat?"
        )
        speak(greeting, voice_name=args.voice)

    log("\nČekám na vaši řeč (mluvte do mikrofonu)...")
    emit_event("listening_idle")

    buffer = []
    is_speaking = False
    silence_start = None
    last_partial_time = 0
    max_peak_seen = 0.0
    consecutive_digital_zeros = 0

    partial_lock = threading.Lock()
    is_partial_running = False

    def run_partial(audio_copy):
        nonlocal is_partial_running
        try:
            res = mlx_whisper.transcribe(
                audio_copy,
                path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                language=args.language,
                condition_on_previous_text=False
            )
            text = res["text"].strip()
            if text and not is_hallucination(text):
                log(f"\r[ŽIVĚ]: \"{text}\" ...", end="")
                emit_event("partial", {"text": text})
        except Exception:
            pass
        finally:
            with partial_lock:
                is_partial_running = False

    # Audio input stream
    stream_kwargs = {
        "samplerate": SAMPLE_RATE,
        "channels": 1,
        "dtype": "float32",
        "blocksize": BLOCK_SIZE,
    }
    if args.device:
        try:
            stream_kwargs["device"] = int(args.device) if args.device.isdigit() else args.device
        except Exception:
            pass

    with sd.InputStream(**stream_kwargs) as stream:
        last_was_zero = True
        while not stop_requested:
            chunk, _ = stream.read(BLOCK_SIZE)
            now = time.time()

            chunk_data = chunk.flatten()
            rms = float(np.sqrt(np.mean(chunk_data**2)))
            peak = float(np.max(np.abs(chunk_data)))
            zero_ratio = float(np.sum(chunk_data == 0.0) / len(chunk_data))
            is_digital_zero = (zero_ratio > 0.95 and rms < 0.0001)

            # Check for physical microphone hardware button interruption while Zuzana is speaking or in cooldown
            if is_tts_playing or now < tts_cooldown_until:
                interrupted = False
                interrupt_reason = ""

                # 1. User was in muted state (digital zero) and presses hardware button to unmute and speak
                if last_was_zero and not is_digital_zero and zero_ratio < 0.85:
                    interrupted = True
                    interrupt_reason = "TLAČÍTKO MIKROFONU (odtlumeno)"
                # 2. User presses hardware button while active (digital zero transition)
                elif (not last_was_zero) and is_digital_zero:
                    interrupted = True
                    interrupt_reason = "TLAČÍTKO MIKROFONU (stisknuto)"

                last_was_zero = is_digital_zero

                if interrupted:
                    log(f"\n[🛑 TLAČÍTKO MIKROFONU - PŘERUŠUJI ZUZANU]: {interrupt_reason}")
                    stop_tts()
                    emit_event("button_press")
                    emit_event("speech_interrupted", {"reason": interrupt_reason})

                    buffer = []
                    silence_start = None
                    consecutive_digital_zeros = 0

                    if rms > SPEECH_START_THRESHOLD:
                        is_speaking = True
                        buffer = [chunk_data]
                        max_peak_seen = peak
                        last_partial_time = now
                        log("\n[POSLOUCHÁM...] ", end="")
                        emit_event("speech_start", {"rms": rms})
                    else:
                        is_speaking = False
                        emit_event("listening_idle")
                        log("\n[PŘIPRAVEN - POSLOUCHÁM...] ")
                    continue
                else:
                    # Still speaking and button not pressed -> discard speaker sound (prevent echo)
                    buffer = []
                    is_speaking = False
                    silence_start = None
                    consecutive_digital_zeros = 0
                    continue

            last_was_zero = is_digital_zero

            # Detect digital zero for microphone hardware button / mute switch
            if is_digital_zero:
                consecutive_digital_zeros += 1
            else:
                consecutive_digital_zeros = 0

            # Start of speech
            if not is_speaking:
                if rms > SPEECH_START_THRESHOLD:
                    is_speaking = True
                    buffer = [chunk_data]
                    max_peak_seen = peak
                    silence_start = None
                    last_partial_time = now
                    consecutive_digital_zeros = 0
                    log("\n[POSLOUCHÁM...] ", end="")
                    emit_event("speech_start", {"rms": rms})
            else:
                # Actively recording speech
                buffer.append(chunk_data)
                max_peak_seen = max(max_peak_seen, peak)

                if rms > SPEECH_CONTINUE_THRESHOLD:
                    # Speech continues -> reset silence timer!
                    if silence_start is not None:
                        silence_start = None
                        log("\r" + " " * 50 + "\r[POSLOUCHÁM...] ", end="")
                        emit_event("speech_resumed")

                    # Periodic live preview
                    duration_so_far = len(buffer) * (BLOCK_SIZE / SAMPLE_RATE)
                    if (now - last_partial_time >= PARTIAL_INTERVAL) and duration_so_far >= 0.8:
                        with partial_lock:
                            if not is_partial_running:
                                is_partial_running = True
                                last_partial_time = now
                                current_audio = np.concatenate(buffer)
                                t = threading.Thread(target=run_partial, args=(current_audio,), daemon=True)
                                t.start()
                else:
                    # Silence or button press
                    should_finish = False
                    finish_reason = ""

                    if consecutive_digital_zeros >= DEBOUNCE_ZERO_CHUNKS:
                        should_finish = True
                        finish_reason = "TLAČÍTKO MIKROFONU (okamžité odeslání)"
                        emit_event("button_press")
                    else:
                        if silence_start is None:
                            silence_start = now

                        elapsed_silence = now - silence_start
                        remaining = max(0.0, SILENCE_TIMEOUT - elapsed_silence)
                        log(f"\r[PAUZA] Čekám... ({remaining:.1f} s zbývá) ", end="")
                        emit_event("silence_countdown", {"remaining": round(remaining, 1)})

                        if elapsed_silence >= SILENCE_TIMEOUT:
                            should_finish = True
                            finish_reason = f"VYPRŠEL LIMIT TICHA ({SILENCE_TIMEOUT:.0f} s)"

                    if should_finish:
                        is_speaking = False
                        silence_start = None
                        consecutive_digital_zeros = 0

                        total_duration = len(buffer) * (BLOCK_SIZE / SAMPLE_RATE)
                        if total_duration >= MIN_SPEECH_DURATION and max_peak_seen >= PEAK_MIN_THRESHOLD:
                            if "TLAČÍTKO" in finish_reason and len(buffer) > DEBOUNCE_ZERO_CHUNKS:
                                audio_final = np.concatenate(buffer[:-DEBOUNCE_ZERO_CHUNKS])
                            else:
                                audio_final = np.concatenate(buffer)

                            log(f"\n[DOKONČUJI ZÁZNAM] ({finish_reason})")
                            emit_event("transcribing_start", {"reason": finish_reason, "duration": round(total_duration, 2)})

                            t0 = time.time()
                            try:
                                result = mlx_whisper.transcribe(
                                    audio_final,
                                    path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                                    language=args.language
                                )
                                whisper_time = time.time() - t0
                                user_text = result["text"].strip()

                                if user_text and not is_hallucination(user_text):
                                    log("=" * 65)
                                    log(f"👤 VY ({whisper_time:.2f} s): \"{user_text}\"")
                                    log("=" * 65)
                                    emit_event("user_speech", {
                                        "text": user_text,
                                        "whisper_seconds": round(whisper_time, 2)
                                    })

                                    # Fast memory wipe command check
                                    is_wipe = False
                                    if args.language == "en":
                                        is_wipe = any(w in user_text.lower() for w in ["clear memory", "forget everything", "start over", "reset conversation"])
                                    else:
                                        is_wipe = any(w in user_text.lower() for w in ["vymaž paměť", "začni znovu", "zapomeň všechno"])

                                    if is_wipe:
                                        conversation_history = [{"role": "system", "content": system_prompt}]
                                        emit_event("memory_cleared")
                                        wipe_msg = "Conversation memory was cleared. What would you like to talk about now?" if args.language == "en" else "Paměť konverzace byla vymazána. O čem si popovídáme teď?"
                                        speak(wipe_msg, voice_name=args.voice)
                                    elif args.managed:
                                        # In managed mode, controller handles query and speaks via stdin command
                                        # But we can also check if agent-url is configured
                                        if args.agent_url:
                                            try:
                                                req_body = json.dumps({"message": user_text, "session_id": "voice"}).encode("utf-8")
                                                req = urllib.request.Request(
                                                    args.agent_url,
                                                    data=req_body,
                                                    headers={"Content-Type": "application/json"}
                                                )
                                                with urllib.request.urlopen(req, timeout=45) as resp:
                                                    resp_data = json.loads(resp.read().decode("utf-8"))
                                                    ans = resp_data.get("response", "")
                                                    if ans:
                                                        speak(ans, voice_name=args.voice)
                                            except Exception as e:
                                                log(f"[CHYBA AGENT API]: {e}")
                                                speak("Omlouvám se, nepodařilo se mi spojit s agentem.", voice_name=args.voice)
                                    else:
                                        # Standalone mode: Query local LM Studio
                                        conversation_history.append({"role": "user", "content": user_text})
                                        log("🤖 Nemotron přemýšlí...", end="", flush=True)
                                        t_llm0 = time.time()
                                        try:
                                            ai_response = query_llm(conversation_history)
                                            llm_time = time.time() - t_llm0
                                            log(f" ({llm_time:.2f} s)")
                                            conversation_history.append({"role": "assistant", "content": ai_response})
                                            if len(conversation_history) > 13:
                                                conversation_history = [conversation_history[0]] + conversation_history[-12:]
                                            speak(ai_response, voice_name=args.voice)
                                        except Exception as e:
                                            log(f"\nChyba při volání LM Studia: {e}")
                                            speak("Omlouvám se, nepodařilo se mi získat odpověď od jazykového modelu.", voice_name=args.voice)
                                else:
                                    log(f"(Ignorován šum / prázdný zvuk: \"{user_text}\")")
                                    emit_event("ignored_noise", {"text": user_text})
                            except Exception as e:
                                log(f"Chyba při přepisu: {e}")
                                emit_event("error", {"error": str(e)})
                        else:
                            log("\r" + " " * 60 + "\r", end="")

                        buffer = []
                        log("\nČekám na další řeč...")
                        emit_event("listening_idle")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nUkončeno uživatelem.")
