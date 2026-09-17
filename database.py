import sqlite3, os, json
DB="probot.db"
def db():
    con=sqlite3.connect(DB)
    con.row_factory=sqlite3.Row
    return con
def init():
    con=db()
    con.execute("CREATE TABLE IF NOT EXISTS warns(chat_id INTEGER, user_id INTEGER, count INTEGER, PRIMARY KEY(chat_id,user_id))")
    con.execute("CREATE TABLE IF NOT EXISTS filters(chat_id INTEGER, keyword TEXT, reply TEXT, PRIMARY KEY(chat_id,keyword))")
    con.execute("CREATE TABLE IF NOT EXISTS notes(chat_id INTEGER, name TEXT, content TEXT, PRIMARY KEY(chat_id,name))")
    con.execute("CREATE TABLE IF NOT EXISTS settings(chat_id INTEGER PRIMARY KEY, welcome TEXT, goodbye TEXT, antiflood INTEGER, warn_limit INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS locks(chat_id INTEGER PRIMARY KEY, flags TEXT)")
    con.commit()
    con.close()
init()
