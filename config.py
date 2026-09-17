import os
from dotenv import load_dotenv
load_dotenv()
BOT_TOKEN = os.getenv("TG_BOT_TOKEN","")
GROQ_API_KEY = os.getenv("GROQ_API_KEY","")
GROQ_MODEL = os.getenv("GROQ_MODEL","openai/gpt-oss-20b")
FF_API = "https://wzapiinfo.vercel.app"
REGISTRY = "https://raw.githubusercontent.com/spotiflacapp/spotiflac-extension/main/registry.json"
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS","8882183155").split(",") if x.strip().isdigit()]
