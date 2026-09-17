import json, urllib.request

REGISTRY_URL = "https://raw.githubusercontent.com/spotiflacapp/spotiflac-extension/main/registry.json"

# Cache registry
_registry_cache = None

def fetch_registry():
    global _registry_cache
    if _registry_cache:
        return _registry_cache
    with urllib.request.urlopen(REGISTRY_URL, timeout=10) as r:
        _registry_cache = json.loads(r.read().decode())
    return _registry_cache

def list_extensions():
    reg = fetch_registry()
    return reg.get("extensions", [])

def get_extension_by_id(ext_id):
    for e in list_extensions():
        if e["id"] == ext_id:
            return e
    return None

def list_download_providers():
    return [e for e in list_extensions() if e["category"] == "download"]

# Quality mapping like app: FLAC, 320, etc.
QUALITIES = ["FLAC", "HI_RES", "320", "256", "128"]

# Lyrics providers: apple-music etc.
def list_lyrics_providers():
    return [e for e in list_extensions() if "lyrics" in e.get("tags", [])]

# SpotiFLAC Python Module wrapper (if installed)
try:
    from SpotiFLAC import SpotiFLAC as _SpotiFLAC
    HAS_MODULE = True
except:
    HAS_MODULE = False
    _SpotiFLAC = None

class SpotiService:
    def __init__(self, registry_url=REGISTRY_URL, quality="FLAC", lyrics=True):
        self.registry_url = registry_url
        self.quality = quality
        self.lyrics = lyrics
        self.registry = fetch_registry()

    def info(self):
        return {
            "registry": self.registry_url,
            "quality": self.quality,
            "lyrics": self.lyrics,
            "extensions": len(self.registry.get("extensions",[])),
            "providers": [p["display_name"] for p in list_download_providers()],
            "has_module": HAS_MODULE,
        }

    def download(self, spotify_url: str, quality: str = None, with_lyrics: bool = None):
        q = quality or self.quality
        l = with_lyrics if with_lyrics is not None else self.lyrics
        if not HAS_MODULE:
            return {"success": False, "error": "SpotiFLAC module not installed. Run pip install SpotiFLAC and configure registry.", "url": spotify_url, "quality": q}
        import tempfile, os, glob
        # map app quality to SpotiFLAC quality
        qmap = {"FLAC": "LOSSLESS", "HI_RES": "HI_RES", "320": "320", "256": "256", "128": "128"}
        sflac_q = qmap.get(q, "LOSSLESS")
        out_dir = tempfile.mkdtemp(prefix="spoti_")
        try:
            # Sync call — pass registry so extensions auto-install (tidal/deezer/qobuz etc)
            _SpotiFLAC(spotify_url, out_dir, quality=sflac_q, embed_lyrics=l, log_level=20, registries=[self.registry_url])
            files = glob.glob(os.path.join(out_dir, "**", "*"), recursive=True)
            audio = [f for f in files if os.path.isfile(f) and f.lower().endswith((".flac",".wav",".mp3",".m4a",".ogg",".opus"))]
            if not audio:
                return {"success": False, "error": "Download finished but no audio file found. Check URL or extension.", "url": spotify_url, "out_dir": out_dir}
            return {"success": True, "url": spotify_url, "quality": q, "sflac_quality": sflac_q, "files": audio, "out_dir": out_dir}
        except Exception as e:
            return {"success": False, "error": f"SpotiFLAC error: {e}", "url": spotify_url, "out_dir": out_dir}
