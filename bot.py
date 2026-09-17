#!/usr/bin/env python3
import json, logging, re, threading, os, asyncio
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes
import telegram.ext.filters as tg_filters
from config import BOT_TOKEN, ADMIN_IDS, GROQ_MODEL, ALLOWED_MODELS
from database import db, _db_lock
import groq_client
import ff_service
import spoti_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("pro-bot")

CONFIG_PATH = Path("app_config.json")
CFG_LOCK = threading.Lock()

# --- Helpers ---
def _load_cfg():
    with CFG_LOCK:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            log.warning("config load failed: %s", e)
            return {}

def _save_cfg(cfg: dict):
    with CFG_LOCK:
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        tmp.replace(CONFIG_PATH)

def _update_env(key: str, value: str):
    env_path = Path(".env")
    try:
        content = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        lines = content.splitlines()
        found = False
        for i, l in enumerate(lines):
            if l.startswith(f"{key}="):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}")
        tmp = env_path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(env_path)
    except OSError as e:
        log.error("env write failed %s: %s", key, e)

async def _is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_user and update.effective_user.id in ADMIN_IDS:
        return True
    if not update.effective_chat or update.effective_chat.type == "private":
        return False
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return m.status in ("administrator", "creator")
    except Exception as e:
        log.debug("get_chat_member failed: %s", e)
        return False

def admin_only(func):
    async def w(update, context):
        if not await _is_admin(update, context):
            await update.message.reply_text("❌ Admin only")
            return
        return await func(update, context)
    return w

# --- Start / Help (Miss Rose style) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Pro Bot — Miss Rose Style</b>\n\n"
        "Group • Channel • Personal Assistant\n\n"
        "Use /help to see all commands",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Help", callback_data="help")],
            [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin")]
        ])
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info("help_cmd from %s chat %s", update.effective_user.id, update.effective_chat.id)
    cfg = _load_cfg()
    prefix = cfg.get("prefix", "/")
    ai_model = cfg.get("ai_chat",{}).get("model", GROQ_MODEL)
    text = (
        f"📖 <b>All Commands</b> (Prefix: <code>{prefix}</code>)\n\n"
        "<b>👮 Admin</b>\n"
        f"{prefix}ban /unban /kick /mute /unmute /pin /unpin /promote /demote\n"
        f"{prefix}warn /warns /resetwarn (3 warns = mute)\n"
        f"{prefix}addadmin /removeadmin /adminlist — Bot admins\n"
        f"{prefix}setprefix <new> — Change prefix (/, !, .)\n"
        f"{prefix}setmodel <model> | {prefix}models — AI model ({ai_model})\n"
        f"{prefix}id /uid /tgid /info — TG UID checker\n\n"
        "<b>🔧 Filters & Notes</b>\n"
        f"{prefix}filter <word> <reply> | {prefix}filters | {prefix}stop <word>\n"
        f"{prefix}save <name> <content> | #<name> | {prefix}notes | {prefix}clear <name>\n\n"
        "<b>👋 Welcome & Locks</b>\n"
        f"{prefix}setwelcome <text> | {prefix}welcome on/off\n"
        f"{prefix}lock <media/links/sticker> | {prefix}unlock | {prefix}locks\n\n"
        "<b>💳 bKash</b>\n"
        f"{prefix}pay | 100 tk pay korbo (auto)\n\n"
        "<b>🎵 SpotiFLAC</b>\n"
        f"{prefix}download <spotify_url>\n\n"
        "<b>🎮 FF Info</b>\n"
        f"{prefix}ffinfo 3941516359\n\n"
        "<b>🤖 AI</b>\n"
        f"{prefix}ai_on /ai_off /ai\n\n"
        f"Support: @ShahrialAmin | Prefix: {prefix} | Use {prefix}help for this"
    )
    kb = [
        [InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin"), InlineKeyboardButton("📖 Help", callback_data="help")],
        [InlineKeyboardButton("🤖 AI Model", callback_data="admin_model"), InlineKeyboardButton("❌ Close", callback_data="admin_close")],
    ]
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

# --- Admin ---
async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to user to /ban")
        return
    user = update.message.reply_to_message.from_user
    await context.bot.ban_chat_member(update.effective_chat.id, user.id)
    await update.message.reply_text(f"✅ Banned {user.mention_html()}", parse_mode="HTML")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = int(context.args[0]) if context.args else None
    if not uid and update.message.reply_to_message:
        uid = update.message.reply_to_message.from_user.id
    if not uid:
        await update.message.reply_text("Usage: /unban <user_id> or reply")
        return
    await context.bot.unban_chat_member(update.effective_chat.id, uid)
    await update.message.reply_text("✅ Unbanned")

async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to mute")
        return
    user = update.message.reply_to_message.from_user
    await context.bot.restrict_chat_member(
        update.effective_chat.id, user.id,
        permissions=ChatPermissions(can_send_messages=False)
    )
    await update.message.reply_text(f"🔇 Muted {user.mention_html()}", parse_mode="HTML")

# --- Warns ---
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to /warn")
        return
    chat_id = update.effective_chat.id
    user = update.message.reply_to_message.from_user
    with _db_lock:
        con = db(); cur = con.cursor()
        cur.execute("INSERT OR IGNORE INTO warns VALUES (?,?,0)", (chat_id, user.id))
        cur.execute("UPDATE warns SET count=count+1 WHERE chat_id=? AND user_id=?", (chat_id, user.id))
        cur.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user.id))
        cnt = cur.fetchone()[0]
        con.commit(); con.close()
    await update.message.reply_text(f"⚠️ Warn {cnt}/3 for {user.mention_html()}", parse_mode="HTML")
    if cnt >= 3:
        await context.bot.restrict_chat_member(
            chat_id, user.id, permissions=ChatPermissions(can_send_messages=False)
        )
        await update.message.reply_text("🔇 3 warns → muted")

async def warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.reply_to_message.from_user if update.message.reply_to_message else update.effective_user
    con = db(); cur = con.cursor()
    cur.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (update.effective_chat.id, user.id))
    row = cur.fetchone()
    con.close()
    cnt = row[0] if row else 0
    await update.message.reply_text(f"Warns for {user.mention_html()}: {cnt}/3", parse_mode="HTML")

# --- Filters ---
async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /filter <keyword> <reply>")
        return
    kw = context.args[0].lower()
    reply = " ".join(context.args[1:])
    with _db_lock:
        con = db(); con.execute("INSERT OR REPLACE INTO filters VALUES (?,?,?)", (update.effective_chat.id, kw, reply)); con.commit(); con.close()
    await update.message.reply_text(f"✅ Filter saved: {kw}")

async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db(); rows = con.execute("SELECT keyword FROM filters WHERE chat_id=?", (update.effective_chat.id,)).fetchall(); con.close()
    await update.message.reply_text("Filters: " + ", ".join([r[0] for r in rows]) if rows else "No filters")

# --- Notes ---
async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /save <name> <content>")
        return
    name = context.args[0].lower()
    content = " ".join(context.args[1:])
    with _db_lock:
        con = db(); con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?)", (update.effective_chat.id, name, content)); con.commit(); con.close()
    await update.message.reply_text(f"✅ Saved #{name}")

async def get_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if text.startswith("#"):
        name = text[1:].split()[0].lower()
        con = db(); row = con.execute("SELECT content FROM notes WHERE chat_id=? AND name=?", (update.effective_chat.id, name)).fetchone(); con.close()
        if row:
            await update.message.reply_text(row[0])

# --- FF Info ---
async def ffinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.args[0] if context.args else "3941516359"
    if not uid.isdigit():
        await update.message.reply_text("Usage: /ffinfo 3941516359")
        return
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        msg = ff_service.format_ff(uid)
        await update.message.reply_text(msg, parse_mode="HTML")
    except Exception as e:
        log.warning("ffinfo %s failed: %s", uid, e)
        await update.message.reply_text(f"❌ FF error: {e}")

# --- SpotiFLAC ---
async def download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /download https://open.spotify.com/track/...")
        return
    url = context.args[0]
    from spoti_service import SpotiService
    svc = SpotiService()
    info = svc.info()
    await update.message.reply_text(f"⬇️ Downloading {url}\nQuality: {info['quality']} | Providers: {len(info['providers'])}\nExtensions will fallback automatically...")
    res = svc.download(url)
    if not res.get("success"):
        await update.message.reply_text(f"⚠️ {res.get('error')}")
    else:
        await update.message.reply_text(f"✅ Queued (mock) — install SpotiFLAC module for real FLAC")

# --- AI ---
async def ai_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_cfg() or {"ai_chat": {"enabled": True}}
    cur = cfg.get("ai_chat", {}).get("enabled", True)
    new = not cur
    cfg.setdefault("ai_chat", {})["enabled"] = new
    cfg["ai_chat"].setdefault("model", GROQ_MODEL)
    _save_cfg(cfg)
    await update.message.reply_text(f"{'✅ AI ON' if new else '❌ AI OFF'}")

async def ai_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_cfg()
    cfg.setdefault("ai_chat", {})["enabled"] = True
    _save_cfg(cfg)
    await update.message.reply_text("✅ AI Enabled")

async def ai_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_cfg()
    cfg.setdefault("ai_chat", {})["enabled"] = False
    _save_cfg(cfg)
    await update.message.reply_text("❌ AI Disabled")

async def setmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    models = ALLOWED_MODELS
    if not context.args:
        cfg = _load_cfg()
        cur = cfg.get("ai_chat", {}).get("model", GROQ_MODEL)
        kb = [[InlineKeyboardButton(f"{'✅ ' if m==cur else ''}{m}", callback_data=f"setmodel_{m}")] for m in models]
        await update.message.reply_text(f"🤖 Current model: <code>{cur}</code>\nSelect new model:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return
    new = context.args[0].strip()
    if new not in models:
        await update.message.reply_text(f"❌ Invalid model. Available:\n" + "\n".join(models))
        return
    cfg = _load_cfg()
    cfg.setdefault("ai_chat", {})["model"] = new
    _save_cfg(cfg)
    _update_env("GROQ_MODEL", new)
    await update.message.reply_text(f"✅ AI model changed to <code>{new}</code>", parse_mode="HTML")

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    models = ALLOWED_MODELS
    cfg = _load_cfg()
    cur = cfg.get("ai_chat", {}).get("model", "unknown")
    await update.message.reply_text("🤖 <b>Available Models</b> (Groq):\n" + "\n".join([f"{'✅ ' if m==cur else '▫️ '}<code>{m}</code>" for m in models]) + f"\n\nCurrent: <code>{cur}</code>\nUse: <code>/setmodel {models[0]}</code>", parse_mode="HTML")

# --- Admin Panel ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    kb = [
        [InlineKeyboardButton("👮 Ban", callback_data="admin_ban"), InlineKeyboardButton("🔓 Unban", callback_data="admin_unban"), InlineKeyboardButton("🔇 Mute", callback_data="admin_mute")],
        [InlineKeyboardButton("⚠️ Warn", callback_data="admin_warn"), InlineKeyboardButton("📋 Warns", callback_data="admin_warns"), InlineKeyboardButton("🔧 Filters", callback_data="admin_filters")],
        [InlineKeyboardButton("📝 Notes", callback_data="admin_notes"), InlineKeyboardButton("💾 Save", callback_data="admin_save"), InlineKeyboardButton("🔒 Locks", callback_data="admin_locks")],
        [InlineKeyboardButton("🤖 AI ON", callback_data="admin_ai_on"), InlineKeyboardButton("🤖 AI OFF", callback_data="admin_ai_off"), InlineKeyboardButton("🧠 Model", callback_data="admin_model")],
        [InlineKeyboardButton("👥 Admins", callback_data="admin_adminlist"), InlineKeyboardButton("➕ Add Admin", callback_data="admin_addadmin"), InlineKeyboardButton("⚙️ Prefix", callback_data="admin_prefix")],
        [InlineKeyboardButton("📖 Help", callback_data="help"), InlineKeyboardButton("❌ Close", callback_data="admin_close")],
    ]
    await update.message.reply_text(
        "⚙️ <b>Admin Panel</b>\n\n"
        "Manage group — select action:\n"
        "• Ban/Mute/Warn for moderation\n"
        "• Filters/Notes for auto replies\n"
        "• AI / Model / Prefix settings",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )

# --- Admin Add / Prefix Change ---
async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    target = None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target = int(context.args[0])
        except (ValueError, TypeError):
            target = None
    if not target:
        await update.message.reply_text("Usage: Reply to user or /addadmin <user_id>")
        return
    cfg = _load_cfg()
    if target not in cfg.get("admin_ids", []):
        cfg.setdefault("admin_ids", []).append(target)
        _save_cfg(cfg)
    await update.message.reply_text(f"✅ Added admin: <code>{target}</code>", parse_mode="HTML")

async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Creator only")
        return
    target = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    if not target:
        await update.message.reply_text("Usage: /removeadmin <user_id> or reply")
        return
    cfg = _load_cfg()
    if target in cfg.get("admin_ids", []):
        cfg["admin_ids"].remove(target)
        _save_cfg(cfg)
    await update.message.reply_text(f"✅ Removed admin: <code>{target}</code>", parse_mode="HTML")

async def adminlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_cfg()
    ids = cfg.get("admin_ids", [])
    await update.message.reply_text("👮 Admins:\n" + "\n".join([f"• <code>{i}</code>" for i in ids]) if ids else "No admins", parse_mode="HTML")

async def uid_checker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /id, /uid, /tgid — shows Telegram IDs
    target_user = None
    target_chat = update.effective_chat
    # if reply, show replied user
    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
    elif context.args and context.args[0].startswith("@"):
        # username lookup not directly possible without extra API, show hint
        await update.message.reply_text("⚠️ Reply to user or use /id without args for your own ID. Username lookup needs reply.")
        return
    elif context.args and context.args[0].isdigit():
        # if numeric id provided, just echo
        uid = context.args[0]
        await update.message.reply_text(f"🆔 Provided UID: <code>{uid}</code>", parse_mode="HTML")
        return
    else:
        target_user = update.effective_user

    text = (
        f"🆔 <b>TG UID Checker</b>\n\n"
        f"👤 User: {target_user.mention_html()} \n"
        f"• ID: <code>{target_user.id}</code>\n"
        f"• Username: @{target_user.username or 'none'}\n"
        f"• Name: {target_user.full_name}\n"
        f"• Is Bot: {target_user.is_bot}\n\n"
        f"💬 Chat:\n"
        f"• Chat ID: <code>{target_chat.id}</code>\n"
        f"• Type: {target_chat.type}\n"
        f"• Title: {target_chat.title or 'Private'}\n"
    )
    # if group, also show message id
    if update.effective_chat.type != "private":
        text += f"\n📌 Message ID: <code>{update.message.message_id}</code>"
    await update.message.reply_text(text, parse_mode="HTML")

async def info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # alias for uid_checker with more details
    await uid_checker(update, context)

async def setprefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _is_admin(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    if not context.args:
        cfg = _load_cfg()
        await update.message.reply_text(f"Current prefix: <code>{cfg.get('prefix','/')}</code>\nUsage: /setprefix !  (or / . )", parse_mode="HTML")
        return
    new = context.args[0].strip()
    if len(new) != 1 or new not in ["/", "!", ".", "~", "#", "$"]:
        await update.message.reply_text("❌ Prefix must be single char: / ! . ~ # $")
        return
    cfg = _load_cfg()
    cfg["prefix"] = new
    _save_cfg(cfg)
    await update.message.reply_text(f"✅ Prefix changed to <code>{new}</code> — now use {new}help", parse_mode="HTML")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    log.info("text_handler text=%r chat=%s user=%s", text[:100], update.effective_chat.id, update.effective_user.id)
    if not text:
        return
    # filter check
    con = db()
    row = con.execute("SELECT reply FROM filters WHERE chat_id=? AND instr(lower(?), keyword) > 0", (update.effective_chat.id, text.lower())).fetchone()
    con.close()
    if row:
        await update.message.reply_text(row[0])
        return
    if text.startswith("#"):
        await get_note(update, context)
        return
    if text.isdigit() and len(text) >= 9:
        try:
            msg = ff_service.format_ff(text)
            await update.message.reply_text(msg, parse_mode="HTML")
            return
        except Exception as e:
            log.debug("auto ff failed %s: %s", text, e)
    if "open.spotify.com" in text:
        await download(update, context)
        return
    m = re.search(r"(\d{2,5})\s*(tk|taka)", text, re.I)
    if m and any(k in text.lower() for k in ["pay", "taka", "tk"]):
        await update.message.reply_text(f"💡 {m.group(1)} Tk pay detected! Use /pay for bKash flow")
        return
    cfg = _load_cfg()
    if cfg.get("ai_chat", {}).get("enabled"):
        try:
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
            reply = groq_client.groq_chat(text)
            log.info("groq reply len=%s", len(reply))
            # Groq may return raw HTML tags (e.g. <spotify-link>) not allowed by Telegram HTML
            try:
                await update.message.reply_text(reply, parse_mode="HTML")
            except Exception as html_e:
                log.warning("HTML send failed (%s), fallback plain", html_e)
                await update.message.reply_text(reply)  # plain, no parse
            return
        except Exception as e:
            log.warning("AI reply failed: %s", e)
            await update.message.reply_text(f"⚠️ AI error: {e}")
            return
    # fallback if AI disabled
    log.info("AI disabled, fallback reply")
    await update.message.reply_text("👋 Hi! Use /help to see commands")

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "help":
        # help via callback — need to send as new message (q.message)
        # create fake update style: reuse help_cmd logic but reply to callback message chat
        cfg = _load_cfg()
        prefix = cfg.get("prefix", "/")
        ai_model = cfg.get("ai_chat", {}).get("model", GROQ_MODEL)
        text = (
            f"📖 <b>All Commands</b> (Prefix: <code>{prefix}</code>)\n\n"
            f"<b>👮 Admin</b>\n{prefix}ban /unban /kick /mute /unmute\n"
            f"{prefix}warn /warns /resetwarn (3 warns = mute)\n"
            f"{prefix}addadmin /removeadmin /adminlist\n"
            f"{prefix}setprefix | {prefix}setmodel | {prefix}models\n"
            f"{prefix}id /uid /info\n\n"
            f"<b>🔧 Filters</b>\n{prefix}filter <word> <reply> | {prefix}filters\n"
            f"<b>📝 Notes</b>\n{prefix}save <name> <content> | #<name>\n\n"
            f"<b>🎮 FF</b> {prefix}ffinfo 3941516359\n"
            f"<b>🎵 Download</b> {prefix}download <spotify_url>\n"
            f"<b>🤖 AI</b> {prefix}ai_on /ai_off /ai\n"
        )
        await q.message.reply_text(text, parse_mode="HTML")
    elif q.data == "admin":
        await admin_panel(update, context)  # fallback
    elif q.data == "admin_close":
        try:
            await q.message.delete()
        except Exception:
            await q.edit_message_text("✅ Closed")
    elif q.data.startswith("admin_"):
        # admin sub-buttons — show usage hint
        hints = {
            "admin_ban": "👮 <b>Ban</b>\nReply to user: <code>/ban</code>",
            "admin_unban": "🔓 <b>Unban</b>\n<code>/unban &lt;user_id&gt;</code> or reply",
            "admin_mute": "🔇 <b>Mute</b>\nReply to user: <code>/mute</code>",
            "admin_warn": "⚠️ <b>Warn</b>\nReply: <code>/warn</code> (3 warns = mute)",
            "admin_warns": "📋 <b>Warns</b>\n<code>/warns</code> (reply or self)",
            "admin_filters": "🔧 <b>Filters</b>\n<code>/filter &lt;word&gt; &lt;reply&gt;</code> | <code>/filters</code>",
            "admin_notes": "📝 <b>Notes</b>\n<code>/save &lt;name&gt; &lt;content&gt;</code> | <code>#name</code>",
            "admin_save": "💾 <b>Save</b>\n<code>/save &lt;name&gt; &lt;content&gt;</code>",
            "admin_locks": "🔒 <b>Locks</b>\n<code>/lock &lt;media/links&gt;</code> | <code>/locks</code>",
            "admin_ai_on": "🤖 <b>AI ON</b>\n<code>/ai_on</code>",
            "admin_ai_off": "🤖 <b>AI OFF</b>\n<code>/ai_off</code>",
            "admin_model": "🧠 <b>Model</b>\n<code>/setmodel</code> or <code>/models</code>",
            "admin_adminlist": "👥 <b>Admins</b>\n<code>/adminlist</code>",
            "admin_addadmin": "➕ <b>Add Admin</b>\nReply: <code>/addadmin</code> or <code>/addadmin &lt;user_id&gt;</code>",
            "admin_prefix": "⚙️ <b>Prefix</b>\n<code>/setprefix !</code> (/, !, ., ~, #, $)",
        }
        hint = hints.get(q.data, "Unknown")
        await q.message.reply_text(hint, parse_mode="HTML")
    elif q.data.startswith("setmodel_"):
        new = q.data.replace("setmodel_", "")
        if new not in ALLOWED_MODELS:
            await q.edit_message_text(f"❌ Invalid model: {new}")
            return
        cfg = _load_cfg()
        cfg.setdefault("ai_chat", {})["model"] = new
        _save_cfg(cfg)
        _update_env("GROQ_MODEL", new)
        await q.edit_message_text(f"✅ AI model → <code>{new}</code>", parse_mode="HTML")

async def _notes_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Notes via #name — use /save <name> <content> to create")

def main():
    if not BOT_TOKEN:
        log.error("TG_BOT_TOKEN not set")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    cfg = _load_cfg()
    prefixes = cfg.get("prefixes", ["/", "!", "."])
    def cmd(names, func):
        for n in names:
            for p in prefixes:
                if p == "/":
                    app.add_handler(CommandHandler(n, func))
                else:
                    app.add_handler(MessageHandler(tg_filters.Regex(rf"^{re.escape(p)}{n}(\s|$)"), func))
    cmd(["start"], start)
    cmd(["help", "commands", "cmd"], help_cmd)
    cmd(["admin", "adminpanel", "panel"], admin_panel)
    cmd(["ban"], ban)
    cmd(["unban"], unban)
    cmd(["mute"], mute)
    cmd(["warn"], warn)
    cmd(["warns", "warnings", "resetwarn"], warns)
    cmd(["filter"], filter_cmd)
    cmd(["filters"], list_filters)
    cmd(["save"], save)
    cmd(["notes"], _notes_list)
    cmd(["addadmin"], addadmin)
    cmd(["removeadmin"], removeadmin)
    cmd(["adminlist", "admins"], adminlist)
    cmd(["setprefix", "prefix"], setprefix)
    cmd(["ffinfo"], ffinfo)
    cmd(["ff"], ffinfo)
    cmd(["player"], ffinfo)
    cmd(["download"], download)
    cmd(["ai"], ai_toggle)
    cmd(["ai_on"], ai_on)
    cmd(["ai_off"], ai_off)
    cmd(["setmodel", "model"], setmodel)
    cmd(["models", "modelist"], models_cmd)
    cmd(["id", "uid", "tgid", "tg_id"], uid_checker)
    cmd(["info"], info_cmd)

    async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
        log.exception("handler error for update %s: %s", update, context.error)

    app.add_error_handler(_on_error)
    app.add_handler(CallbackQueryHandler(callback))
    # log all updates for debug
    async def _log_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
        log.info("recv update_id=%s chat=%s user=%s text=%r", update.update_id, update.effective_chat.id if update.effective_chat else None, update.effective_user.id if update.effective_user else None, (update.message.text[:80] if update.message and update.message.text else ""))
    # add as first handler to log everything (group=-1)
    from telegram.ext import TypeHandler
    app.add_handler(TypeHandler(Update, _log_update), group=-1)
    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(tg_filters.Regex(r"^#\w+"), get_note))

    # Python 3.14 compat
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    # Health server for UptimeRobot (Render) — separate thread, polling remains main loop
    port = int(os.getenv("PORT", "10000"))
    try:
        with open("status.html", encoding="utf-8") as f:
            STATUS_HTML = f.read()
    except (FileNotFoundError, OSError) as e:
        log.warning("status.html missing: %s", e)
        STATUS_HTML = "<html><body><h1>OK - Inaya On TG</h1></body></html>"
    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(STATUS_HTML.encode())
            except BrokenPipeError:
                pass
        def log_message(self, *a):
            pass
    threading.Thread(target=lambda: HTTPServer(("0.0.0.0", port), Health).serve_forever(), daemon=True).start()
    log.info("Health HTML for UptimeRobot on :%s/", port)
    log.info("Pro Bot polling (Miss Rose style)")
    app.run_polling(allowed_updates=["message", "callback_query", "chat_member"])

if __name__=="__main__":
    main()
