#!/usr/bin/env python3
import logging, re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes
import telegram.ext.filters as tg_filters
from config import BOT_TOKEN, ADMIN_IDS
from database import db
import groq_client
import ff_service
import spoti_service

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pro-bot")

# --- Helpers ---
def admin_only(func):
    async def w(update, context):
        if update.effective_user.id not in ADMIN_IDS:
            # also check group admin
            try:
                m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
                if m.status not in ["administrator","creator"]:
                    await update.message.reply_text("❌ Admin only")
                    return
            except:
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
    import json
    cfg = {}
    try:
        cfg = json.load(open("app_config.json"))
    except: pass
    prefix = cfg.get("prefix", "/")
    ai_model = cfg.get("ai_chat",{}).get("model","openai/gpt-oss-20b")
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
    await update.message.reply_text(text, parse_mode="HTML")

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
    await context.bot.restrict_chat_member(update.effective_chat.id, user.id, permissions=None)
    await update.message.reply_text(f"🔇 Muted {user.mention_html()}", parse_mode="HTML")

# --- Warns ---
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("Reply to /warn")
        return
    chat_id = update.effective_chat.id
    user = update.message.reply_to_message.from_user
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO warns VALUES (?,?,0)", (chat_id, user.id))
    cur.execute("UPDATE warns SET count=count+1 WHERE chat_id=? AND user_id=?", (chat_id, user.id))
    cur.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user.id))
    cnt = cur.fetchone()[0]
    con.commit(); con.close()
    await update.message.reply_text(f"⚠️ Warn {cnt}/3 for {user.mention_html()}", parse_mode="HTML")
    if cnt >= 3:
        await context.bot.restrict_chat_member(chat_id, user.id, permissions=None)
        await update.message.reply_text("🔇 3 warns → muted")

async def warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.reply_to_message.from_user if update.message.reply_to_message else update.effective_user
    con = db(); cur = con.cursor()
    cur.execute("SELECT count FROM warns WHERE chat_id=? AND user_id=?", (update.effective_chat.id, user.id))
    row = cur.fetchone()
    cnt = row[0] if row else 0
    await update.message.reply_text(f"Warns for {user.mention_html()}: {cnt}/3", parse_mode="HTML")

# --- Filters ---
async def filter_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /filter <keyword> <reply>")
        return
    kw = context.args[0].lower()
    reply = " ".join(context.args[1:])
    con = db(); con.execute("INSERT OR REPLACE INTO filters VALUES (?,?,?)", (update.effective_chat.id, kw, reply)); con.commit(); con.close()
    await update.message.reply_text(f"✅ Filter saved: {kw}")

async def list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db(); rows = con.execute("SELECT keyword FROM filters WHERE chat_id=?", (update.effective_chat.id,)).fetchall()
    await update.message.reply_text("Filters: " + ", ".join([r[0] for r in rows]) if rows else "No filters")

# --- Notes ---
async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /save <name> <content>")
        return
    name = context.args[0].lower()
    content = " ".join(context.args[1:])
    con = db(); con.execute("INSERT OR REPLACE INTO notes VALUES (?,?,?)", (update.effective_chat.id, name, content)); con.commit(); con.close()
    await update.message.reply_text(f"✅ Saved #{name}")

async def get_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text.startswith("#"):
        name = text[1:].split()[0].lower()
        con = db(); row = con.execute("SELECT content FROM notes WHERE chat_id=? AND name=?", (update.effective_chat.id, name)).fetchone()
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
    # simple toggle via file
    import json, os
    cfg_path = "app_config.json"
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except:
        cfg = {"ai_chat":{"enabled":True}}
    cur = cfg.get("ai_chat",{}).get("enabled",True)
    new = not cur
    cfg["ai_chat"] = {"enabled": new, "model":"openai/gpt-oss-20b"}
    with open(cfg_path,"w") as f:
        json.dump(cfg,f,indent=2)
    await update.message.reply_text(f"{'✅ AI ON' if new else '❌ AI OFF'}")

async def ai_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json
    cfg=json.load(open("app_config.json"))
    cfg["ai_chat"]["enabled"]=True
    open("app_config.json","w").write(json.dumps(cfg,indent=2))
    await update.message.reply_text("✅ AI Enabled")

async def ai_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json
    cfg=json.load(open("app_config.json"))
    cfg["ai_chat"]["enabled"]=False
    open("app_config.json","w").write(json.dumps(cfg,indent=2))
    await update.message.reply_text("❌ AI Disabled")

async def setmodel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json
    if not await is_admin_check(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    models = ["openai/gpt-oss-20b","openai/gpt-oss-120b","qwen/qwen3.8-27b","groq/compound","groq/compound-mini","allam-2-7b"]
    if not context.args:
        cfg=json.load(open("app_config.json"))
        cur=cfg.get("ai_chat",{}).get("model","openai/gpt-oss-20b")
        kb=[[InlineKeyboardButton(f"{'✅ ' if m==cur else ''}{m}", callback_data=f"setmodel_{m}")] for m in models]
        await update.message.reply_text(f"🤖 Current model: <code>{cur}</code>\nSelect new model:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return
    new = context.args[0].strip()
    if new not in models:
        await update.message.reply_text(f"❌ Invalid model. Available:\n" + "\n".join(models))
        return
    cfg=json.load(open("app_config.json"))
    cfg["ai_chat"]["model"]=new
    open("app_config.json","w").write(json.dumps(cfg,indent=2, ensure_ascii=False))
    # also update .env for groq_client
    try:
        env=open(".env").read()
        if "GROQ_MODEL" in env:
            env="\n".join([f"GROQ_MODEL={new}" if l.startswith("GROQ_MODEL") else l for l in env.split("\n")])
        else:
            env+=f"\nGROQ_MODEL={new}\n"
        open(".env","w").write(env)
    except: pass
    await update.message.reply_text(f"✅ AI model changed to <code>{new}</code>", parse_mode="HTML")

async def models_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    models = ["openai/gpt-oss-20b","openai/gpt-oss-120b","qwen/qwen3.8-27b","groq/compound","groq/compound-mini","allam-2-7b"]
    import json
    cfg=json.load(open("app_config.json"))
    cur=cfg.get("ai_chat",{}).get("model","unknown")
    await update.message.reply_text("🤖 <b>Available Models</b> (Groq):\n" + "\n".join([f"{'✅ ' if m==cur else '▫️ '}<code>{m}</code>" for m in models]) + f"\n\nCurrent: <code>{cur}</code>\nUse: <code>/setmodel {models[0]}</code>", parse_mode="HTML")

# --- Admin Add / Prefix Change ---
async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json
    if update.effective_user.id not in [8882183155] and not await is_admin(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    target = None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    elif context.args:
        try: target = int(context.args[0])
        except: pass
    if not target:
        await update.message.reply_text("Usage: Reply to user or /addadmin <user_id>")
        return
    cfg = json.load(open("app_config.json"))
    if target not in cfg["admin_ids"]:
        cfg["admin_ids"].append(target)
        open("app_config.json","w").write(json.dumps(cfg,indent=2, ensure_ascii=False))
    await update.message.reply_text(f"✅ Added admin: <code>{target}</code>", parse_mode="HTML")

async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json
    if update.effective_user.id not in [8882183155]:
        await update.message.reply_text("❌ Creator only")
        return
    target = int(context.args[0]) if context.args and context.args[0].isdigit() else None
    if update.message.reply_to_message:
        target = update.message.reply_to_message.from_user.id
    if not target:
        await update.message.reply_text("Usage: /removeadmin <user_id> or reply")
        return
    cfg = json.load(open("app_config.json"))
    if target in cfg["admin_ids"]:
        cfg["admin_ids"].remove(target)
        open("app_config.json","w").write(json.dumps(cfg,indent=2, ensure_ascii=False))
    await update.message.reply_text(f"✅ Removed admin: <code>{target}</code>", parse_mode="HTML")

async def adminlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import json
    cfg = json.load(open("app_config.json"))
    ids = cfg.get("admin_ids",[])
    await update.message.reply_text("👮 Admins:\n" + "\n".join([f"• <code>{i}</code>" for i in ids]), parse_mode="HTML")

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
    import json
    if not await is_admin_check(update, context):
        await update.message.reply_text("❌ Admin only")
        return
    if not context.args:
        cfg = json.load(open("app_config.json"))
        await update.message.reply_text(f"Current prefix: <code>{cfg.get('prefix','/')}</code>\nUsage: /setprefix !  (or / . )", parse_mode="HTML")
        return
    new = context.args[0].strip()
    if len(new)!=1 or new not in ["!",".","/","~","#","$"]:
        await update.message.reply_text("❌ Prefix must be single char: / ! . ~ # $")
        return
    cfg = json.load(open("app_config.json"))
    cfg["prefix"] = new
    open("app_config.json","w").write(json.dumps(cfg,indent=2, ensure_ascii=False))
    await update.message.reply_text(f"✅ Prefix changed to <code>{new}</code> — now use {new}help", parse_mode="HTML")

async def is_admin_check(update, context):
    if update.effective_user.id in [8882183155]:
        return True
    try:
        m = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return m.status in ["administrator","creator"]
    except:
        return False

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    # filter check
    con = db(); row = con.execute("SELECT reply FROM filters WHERE chat_id=? AND lower(?) LIKE '%'||keyword||'%'", (update.effective_chat.id, text.lower())).fetchone()
    if row:
        await update.message.reply_text(row[0])
        return
    # note check done via separate handler, but also here for #note
    if text.startswith("#"):
        await get_note(update, context)
        return
    # FF UID auto-detect
    if text.isdigit() and len(text)>=9:
        try:
            msg = ff_service.format_ff(text)
            await update.message.reply_text(msg, parse_mode="HTML")
            return
        except: pass
    # Spotify link auto
    if "open.spotify.com" in text:
        await download(update, context)
        return
    # pay intent
    import re
    m=re.search(r"(\d{2,5})\s*(tk|taka)", text, re.I)
    if m and any(k in text.lower() for k in ["pay","taka","tk"]):
        await update.message.reply_text(f"💡 {m.group(1)} Tk pay detected! Use /pay for bKash flow")
        return
    # AI fallback
    import json
    try:
        cfg=json.load(open("app_config.json"))
        if cfg.get("ai_chat",{}).get("enabled"):
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
            reply = groq_client.groq_chat(text)
            await update.message.reply_text(reply, parse_mode="HTML")
    except:
        pass

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data=="help":
        await help_cmd(update, context)
    elif q.data=="admin":
        await update.callback_query.message.reply_text("Admin panel: /ban /warn /filter etc.")
    elif q.data.startswith("setmodel_"):
        import json
        new=q.data.replace("setmodel_","")
        cfg=json.load(open("app_config.json"))
        cfg["ai_chat"]["model"]=new
        open("app_config.json","w").write(json.dumps(cfg,indent=2, ensure_ascii=False))
        try:
            env=open(".env").read()
            if "GROQ_MODEL" in env:
                env="\n".join([f"GROQ_MODEL={new}" if l.startswith("GROQ_MODEL") else l for l in env.split("\n")])
            else:
                env+=f"\nGROQ_MODEL={new}\n"
            open(".env","w").write(env)
        except: pass
        await q.edit_message_text(f"✅ AI model → <code>{new}</code>", parse_mode="HTML")

def main():
    if not BOT_TOKEN:
        print("Set TG_BOT_TOKEN in .env")
        return
    app = Application.builder().token(BOT_TOKEN).build()
    # core — support multiple prefixes via custom handlers
    import json
    try:
        cfg=json.load(open("app_config.json"))
        prefixes=cfg.get("prefixes",["/","!","."])
    except:
        prefixes=["/","!","."]
    # helper to register command with all prefixes
    def cmd(names, func):
        for n in names:
            for p in prefixes:
                # PTB CommandHandler only handles / by default, so we use MessageHandler for custom prefixes
                if p=="/":
                    app.add_handler(CommandHandler(n, func))
                else:
                    app.add_handler(MessageHandler(tg_filters.Regex(rf"^{re.escape(p)}{n}(\s|$)"), func))
    # core
    cmd(["start"], start)
    cmd(["help","commands","cmd"], help_cmd)
    # admin
    cmd(["ban"], ban)
    cmd(["unban"], unban)
    cmd(["mute"], mute)
    # warns
    cmd(["warn"], warn)
    cmd(["warns","warnings","resetwarn"], warns)
    # filters/notes
    cmd(["filter"], filter_cmd)
    cmd(["filters"], list_filters)
    cmd(["save"], save)
    cmd(["notes"], lambda u,c: u.message.reply_text("Notes via #name"))
    cmd(["addadmin"], addadmin)
    cmd(["removeadmin"], removeadmin)
    cmd(["adminlist","admins"], adminlist)
    cmd(["setprefix","prefix"], setprefix)
    # ff
    cmd(["ffinfo"], ffinfo)
    cmd(["ff"], ffinfo)
    cmd(["player"], ffinfo)
    # spotiflac
    cmd(["download"], download)
    # ai
    cmd(["ai"], ai_toggle)
    cmd(["ai_on"], ai_on)
    cmd(["ai_off"], ai_off)
    cmd(["setmodel","model"], setmodel)
    cmd(["models","modelist"], models_cmd)
    # uid checker
    cmd(["id","uid","tgid","tg_id"], uid_checker)
    cmd(["info"], info_cmd)
    # callbacks
    app.add_handler(CallbackQueryHandler(callback))
    # text
    app.add_handler(MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, text_handler))
    # also handle notes with # prefix
    app.add_handler(MessageHandler(tg_filters.Regex(r"^#\w+"), get_note))

    print("Pro Bot polling (Miss Rose style)...")
    app.run_polling()

if __name__=="__main__":
    main()
