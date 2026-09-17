import json
import urllib.request
import urllib.error
from config import GROQ_API_KEY, GROQ_MODEL

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "You are an expert assistant for a multi-feature Telegram bot. You know EVERYTHING about this bot:\n\n"
    "BOT FEATURES:\n"
    "1. bKash Payment (Personal): 4 methods — 📱 Sender Verify (amount+sender), 🆔 TrxID Verify (TrxID+amount), ⚡ Auto Detect (amount only), 🔐 Dual (sender+TrxID). User says '100 tk pay korbo' -> auto detect amount + method select. Admin can toggle methods, set bKash number, amounts via /admin.\n"
    "2. SpotiFLAC Music: Registry https://raw.githubusercontent.com/spotiflacapp/spotiflac-extension/main/registry.json with 8 extensions (spotify-web, amazon, apple-music, soundcloud, ytmusic-spotiflac, deezer, qobuz-web, tidal-web). Qualities: FLAC, HI_RES, 320, 256, 128. Lyrics via apple-music. Download via Spotify links. Use /download <spotify_url> or ask naturally.\n"
    "3. FF Info: /ffinfo <uid> or /ff <uid> or just numeric UID (>=9 digits). Uses https://wzapiinfo.vercel.app/get?uid= . Testing UID 3941516359 (BD, Lv39). Also /api/player?uid= via local ff-info-api/server.js. Shows nickname, region, level, rank, likes, clan, pet.\n"
    "4. AI Chat: Groq (openai/gpt-oss-20b, vision qwen/qwen3.8-27b, whisper whisper-large-v3-turbo). Enabled via /ai_on, disabled via /ai_off, toggle /ai or Admin Panel AI button. When enabled, any normal text (no prefix) -> AI reply. Also supports image (vision) and voice (transcribe) when AI ON.\n"
    "5. Keyboards: Reply Keyboard (💳 Pay Now, etc.) for main flow, Inline for admin. Try Again button on fail.\n"
    "6. Commands: /start (welcome), /pay (amount select), /help (this help), /admin (admin panel), /ai_on|/ai_off|/ai, /ffinfo, /ff, /player, /download\n"
    "7. Support: @ShahrialAmin, bKash Personal number in config, FF BR/BD etc regions.\n"
    "Answer concisely, friendly, in Banglish (Bangla phonetic + English) unless user wants pure Bangla/English. Keep technical terms in English. Be concise. You can see images and hear voice transcriptions. If user asks about any feature, explain step-by-step. If user says 'help' or 'support', show relevant commands."
)

VISION_MODEL = "qwen/qwen3.8-27b"  # supports image
WHISPER_MODEL = "whisper-large-v3-turbo"

def groq_chat(user_text: str, history: list = None) -> str:
    if not GROQ_API_KEY:
        return "❌ Groq API key not set. Admin ke bolo .env e GROQ_API_KEY bosate."
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (bKashBot/1.0)",
    }
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 512,
    }
    try:
        req = urllib.request.Request(
            GROQ_URL,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode())
            return j["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else str(e)
        print(f"[groq] HTTP {e.code} {body[:600]}")
        if e.code == 401:
            return "❌ Groq API key invalid/expired."
        return f"❌ Groq error {e.code}: {body[:300]}"
    except Exception as e:
        print(f"[groq] error {e}")
        return f"❌ AI error: {e}"

def groq_vision(image_b64: str, prompt: str = "Describe this image in Banglish, concise") -> str:
    if not GROQ_API_KEY:
        return "❌ Groq API key not set."
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (bKashBot/1.0)",
    }
    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]}
        ],
        "max_tokens": 512,
    }
    try:
        req = urllib.request.Request(GROQ_URL, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
            return j["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[groq vision] {e}")
        return f"❌ Vision error: {e}"

def groq_transcribe(file_path: str) -> str:
    import mimetypes, os
    if not GROQ_API_KEY:
        return "❌ Groq API key not set."
    import http.client, mimetypes, uuid
    boundary = uuid.uuid4().hex
    with open(file_path, "rb") as f:
        file_data = f.read()
    filename = os.path.basename(file_path)
    # Telegram voice is .oga but Groq only allows ogg/opus — rename to .ogg
    if filename.endswith(".oga"):
        filename = filename[:-4] + ".ogg"
    elif "." not in filename:
        filename += ".ogg"
    ctype = mimetypes.guess_type(filename)[0] or "audio/ogg"
    if ctype not in ["audio/ogg", "audio/mpeg", "audio/mp3", "audio/wav", "audio/webm", "audio/flac", "audio/mp4"]:
        ctype = "audio/ogg"
    body = (
        f"--{boundary}\r\n".encode() +
        f'Content-Disposition: form-data; name="model"\r\n\r\n{WHISPER_MODEL}\r\n'.encode() +
        f'--{boundary}\r\n'.encode() +
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode() +
        f'Content-Type: {ctype}\r\n\r\n'.encode() +
        file_data + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "User-Agent": "Mozilla/5.0 (bKashBot/1.0)",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    try:
        req = urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
            return j.get("text", "").strip()
    except urllib.error.HTTPError as e:
        body2 = e.read().decode() if e.fp else str(e)
        print(f"[groq whisper] HTTP {e.code} {body2[:800]}")
        return f"❌ Transcribe error {e.code}: {body2[:400]}"
    except Exception as e:
        print(f"[groq whisper] {e}")
        return f"❌ Transcribe error: {e}"

# quick test helper
if __name__ == "__main__":
    print(groq_chat("hello, tumi ke?"))
