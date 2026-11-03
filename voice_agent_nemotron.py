"""
Full Conversational Voice AI Agent for Mac Mini M4:
- Speech Input: Connect IT ClipMic Pro (USBAudio1.0)
- Speech Recognition: mlx-whisper (whisper-large-v3-turbo, Apple Silicon M4)
- Intelligence: LM Studio Local LLM (nvidia-nemotron-3.5-bolt-e4b-mlx)
- Voice Output: macOS native Zuzana (Premium)
- Features: Real-time streaming preview, 4s pause / instant button submission,
            multi-turn conversation memory, echo-loop protection.
"""

import os
import sys
import time
import re
import json
import threading
import subprocess
import urllib.request
import numpy as np
import sounddevice as sd
import imageio_ffmpeg

# Ensure ffmpeg in PATH for mlx-whisper
ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
os.environ["PATH"] = f"{ffmpeg_dir}:{os.environ.get('PATH', '')}"

import mlx_whisper

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

# LM Studio API Settings
LMSTUDIO_URL = "http://localhost:1234/v1/chat/completions"
LLM_MODEL = "nvidia-nemotron-3.5-bolt-e4b-mlx"

SYSTEM_PROMPT = (
    "Jsi inteligentní, přirozená a pohotová česká hlasová asistentka. "
    "Mluvíš česky. Tvé odpovědi jsou určeny pro předčítání hlasem, proto buď stručná, "
    "mluv k věci a srozumitelně, typicky v rozsahu 1 až 3 vět. "
    "Nikdy nepoužívej formátování markdown (žádné hvězdičky, odrážky, mřížky), "
    "ani emotikony, aby text zněl naprosto přirozeně při vyslovení."
)

HALLUCINATIONS = [
    "titulky", "johnyx", "sledování", "překlad", "časování",
    "konec", "odběr", "zvoneček", "byl jsem rád", "děkuji za zhlédnutí"
]

is_tts_playing = False
tts_cooldown_until = 0.0

def log(msg, end="\n", flush=True):
    sys.stdout.write(msg + end)
    if flush:
        sys.stdout.flush()

def clean_text_for_speech(text):
    """Removes markdown syntax, emojis, and symbols that sound weird when spoken."""
    # Remove markdown formatting like **bold**, *italic*, # Headers, ```code```
    text = re.sub(r'[*_#`~>\[\]\(\)]', '', text)
    # Remove emojis and strange unicode symbols
    text = re.sub(r'[^\w\s.,!?:;\-\'\"]', '', text, flags=re.UNICODE)
    # Clean multiple spaces
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def speak(text):
    """Speaks text using macOS native Zuzana (Premium) and disables mic during speech."""
    global is_tts_playing, tts_cooldown_until
    is_tts_playing = True
    clean_text = clean_text_for_speech(text)
    log(f"\n[ZUZANA MLUVÍ]: \"{clean_text}\"")
    cmd = ["say", "-v", "Zuzana (Premium)", clean_text]
    subprocess.run(cmd)
    # 0.6s room reverberation cooldown
    tts_cooldown_until = time.time() + 0.6
    is_tts_playing = False

def query_llm(messages):
    """Queries LM Studio local API with multi-turn conversation history."""
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 150
    }
    req = urllib.request.Request(
        LMSTUDIO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["choices"][0]["message"]["content"].strip()

def is_hallucination(text):
    low = text.lower().strip()
    if not low:
        return True
    for h in HALLUCINATIONS:
        if h in low:
            return True
    return False

def main():
    global is_tts_playing, tts_cooldown_until

    log("=" * 70)
    log("  Mac Mini M4: HLASOVÝ AGENT S LM STUDIO (Nemotron 3.5 MLX)")
    log("=" * 70)
    log(f"• LLM Model: {LLM_MODEL} (LM Studio na localhost:1234)")
    log("• Hlasový přepis: mlx-whisper (whisper-large-v3-turbo)")
    log("• Hlasový výstup: Zuzana (Premium)")
    log("• Pamatuji si kontext celé konverzace.")
    log("-" * 70)

    # Verify LM Studio connection
    log("Ověřuji spojení s LM Studiem...")
    try:
        check_req = urllib.request.urlopen("http://localhost:1234/v1/models", timeout=3)
        log("LM Studio je online a připraveno!")
    except Exception as e:
        log(f"[VAROVÁNÍ] Nelze se spojit s LM Studiem na localhost:1234: {e}")
        log("Ujistěte se, že v LM Studiu běží Local Server.")
        return

    log("Načítám a předehřívám Whisper na Apple M4...")
    dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mlx_whisper.transcribe(dummy, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language="cs")
    log("Vše je připraveno!")

    # Conversation history
    conversation_history = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    speak("Ahoj! Jsem připravena k povídání. Na co se chceš zeptat?")

    log("\nČekám na vaši řeč (mluvte do mikrofonu)...")

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
                language="cs",
                condition_on_previous_text=False
            )
            text = res["text"].strip()
            if text and not is_hallucination(text):
                log(f"\r[ŽIVĚ]: \"{text}\" ...", end="")
        except Exception:
            pass
        finally:
            with partial_lock:
                is_partial_running = False

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32', blocksize=BLOCK_SIZE) as stream:
        while True:
            chunk, _ = stream.read(BLOCK_SIZE)
            now = time.time()

            if is_tts_playing or now < tts_cooldown_until:
                buffer = []
                is_speaking = False
                silence_start = None
                consecutive_digital_zeros = 0
                continue

            chunk_data = chunk.flatten()
            rms = float(np.sqrt(np.mean(chunk_data**2)))
            peak = float(np.max(np.abs(chunk_data)))
            zero_ratio = float(np.sum(chunk_data == 0.0) / len(chunk_data))

            # Detect digital zero
            if zero_ratio > 0.95 and rms < 0.0001:
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
            else:
                # Actively recording
                buffer.append(chunk_data)
                max_peak_seen = max(max_peak_seen, peak)

                if rms > SPEECH_CONTINUE_THRESHOLD:
                    # Speech continues -> reset silence timer!
                    if silence_start is not None:
                        silence_start = None
                        log("\r" + " " * 50 + "\r[POSLOUCHÁM...] ", end="")

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
                    else:
                        if silence_start is None:
                            silence_start = now

                        elapsed_silence = now - silence_start
                        remaining = max(0.0, SILENCE_TIMEOUT - elapsed_silence)
                        log(f"\r[PAUZA] Čekám... ({remaining:.1f} s zbývá) ", end="")

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

                            t0 = time.time()
                            try:
                                result = mlx_whisper.transcribe(
                                    audio_final,
                                    path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
                                    language="cs"
                                )
                                whisper_time = time.time() - t0
                                user_text = result["text"].strip()

                                if user_text and not is_hallucination(user_text):
                                    log("=" * 65)
                                    log(f"👤 VY ({whisper_time:.2f} s): \"{user_text}\"")
                                    log("=" * 65)

                                    # Special command to clear memory
                                    if any(w in user_text.lower() for w in ["vymaž paměť", "začni znovu", "zapomeň všechno"]):
                                        conversation_history = [{"role": "system", "content": SYSTEM_PROMPT}]
                                        speak("Paměť konverzace byla vymazána. O čem si popovídáme teď?")
                                    else:
                                        # Add user message to history
                                        conversation_history.append({"role": "user", "content": user_text})

                                        # Query LLM (Nemotron in LM Studio)
                                        log("🤖 Nemotron přemýšlí...", end="", flush=True)
                                        t_llm0 = time.time()
                                        try:
                                            ai_response = query_llm(conversation_history)
                                            llm_time = time.time() - t_llm0
                                            log(f" ({llm_time:.2f} s)")

                                            # Add assistant message to history
                                            conversation_history.append({"role": "assistant", "content": ai_response})

                                            # Keep conversation history bounded (last 12 messages)
                                            if len(conversation_history) > 13:
                                                conversation_history = [conversation_history[0]] + conversation_history[-12:]

                                            # Speak answer
                                            speak(ai_response)
                                        except Exception as e:
                                            log(f"\nChyba při volání LM Studia: {e}")
                                            speak("Omlouvám se, nepodařilo se mi získat odpověď od jazykového modelu.")

                                else:
                                    log(f"(Ignorován šum / prázdný zvuk: \"{user_text}\")")
                            except Exception as e:
                                log(f"Chyba při přepisu: {e}")
                        else:
                            log("\r" + " " * 60 + "\r", end="")

                        buffer = []
                        log("\nČekám na další řeč...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nUkončeno.")

