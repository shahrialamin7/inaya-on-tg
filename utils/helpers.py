def is_admin(update, context):
    if not update.effective_chat or update.effective_chat.type=="private":
        return True
    user_id=update.effective_user.id
    try:
        m=context.bot.get_chat_member(update.effective_chat.id, user_id)
        return m.status in ["administrator","creator"]
    except:
        return False
