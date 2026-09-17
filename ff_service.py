import json, urllib.request
UPSTREAM = "https://wzapiinfo.vercel.app"
def fetch_ff(uid: str):
    with urllib.request.urlopen(f"{UPSTREAM}/get?uid={uid}", timeout=8) as r:
        raw = json.loads(r.read().decode())
    if not raw.get("basic_info"):
        raise ValueError("Player not found")
    b = raw["basic_info"]
    clan = raw.get("clan_basic_info", {})
    pet = raw.get("pet_info", {})
    sig = raw.get("social_info", {}).get("signature","")
    return raw, b, clan, pet, sig

def format_ff(uid: str) -> str:
    raw, b, clan, pet, sig = fetch_ff(uid)
    msg = (
        f"🎮 <b>Free Fire Info</b>\n\n"
        f"🆔 UID: <code>{b.get('account_id')}</code>\n"
        f"👤 Nick: <b>{b.get('nickname')}</b>\n"
        f"🌍 Region: {b.get('region')} | Level: {b.get('level')} (Exp {b.get('exp')})\n"
        f"🏆 Rank: {b.get('rank')} ({b.get('ranking_points')} pts)\n"
        f"⚔️ CS Rank: {b.get('cs_rank')} ({b.get('cs_ranking_points')} pts)\n"
        f"❤️ Likes: {b.get('liked')}\n"
        f"🎖️ Badge: {b.get('badge_id')} x{b.get('badge_cnt')}\n"
        f"📅 Created: {b.get('create_at')} | Last: {b.get('last_login_at')}\n"
    )
    if clan and clan.get("clan_name"):
        msg += f"👥 Clan: {clan.get('clan_name')} Lv{clan.get('clan_level')} ({clan.get('member_num')} members)\n"
    if pet and pet.get("id"):
        msg += f"🐾 Pet: {pet.get('id')} Lv{pet.get('level')}\n"
    if sig:
        msg += f"💬 Bio: <i>{sig[:200]}</i>\n"
    return msg
