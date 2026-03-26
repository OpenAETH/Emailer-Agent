"""
Asistente Ejecutivo — Deploy Render
Auth: JWT session  |  IMAP sync (delete-aware)  |  Envio multiple
"""
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import sqlite3, os, smtplib, imaplib, email as email_lib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header
from datetime import datetime, timedelta
import uvicorn, logging, re, secrets, hashlib, hmac, json, time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG desde ENV (Render Dashboard)
# ─────────────────────────────────────────────
def cfg():
    return {
        "smtp_method":  os.getenv("SMTP_METHOD", "smtp_ssl"),
        "smtp_host":    os.getenv("SMTP_HOST", ""),
        "smtp_port":    int(os.getenv("SMTP_PORT", "465")),
        "smtp_user":    os.getenv("SMTP_USER", ""),
        "smtp_pass":    os.getenv("SMTP_PASS", ""),
        "sender_name":  os.getenv("SENDER_NAME", ""),
        "imap_host":    os.getenv("IMAP_HOST", ""),
        "imap_port":    int(os.getenv("IMAP_PORT", "993")),
        "imap_user":    os.getenv("IMAP_USER", ""),
        "imap_pass":    os.getenv("IMAP_PASS", ""),
    }

APP_USER     = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "changeme")
SECRET_KEY   = os.getenv("SECRET_KEY", secrets.token_hex(32))
TOKEN_TTL    = int(os.getenv("TOKEN_TTL_HOURS", "8")) * 3600

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, "data.db")

# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=2000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
        company TEXT, role TEXT, phone TEXT, context TEXT, tags TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS email_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        direction TEXT NOT NULL,
        contact_email TEXT, contact_name TEXT,
        subject TEXT, body TEXT, body_html TEXT,
        intent TEXT, status TEXT DEFAULT 'sent',
        sent_at TEXT, received_at TEXT, replied_at TEXT,
        message_id TEXT, thread_id TEXT, campaign_id TEXT,
        ai_suggestion TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL, entity TEXT, content TEXT NOT NULL,
        importance INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS inbox_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_id TEXT UNIQUE NOT NULL,
        imap_uid TEXT,
        from_email TEXT, from_name TEXT,
        subject TEXT, body TEXT, date TEXT,
        read INTEGER DEFAULT 0, replied INTEGER DEFAULT 0,
        ai_suggestion TEXT, fetched_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL
    );
    """)
    conn.commit()
    # Migraciones seguras
    migrations = [
        "ALTER TABLE email_logs ADD COLUMN body_html TEXT",
        "ALTER TABLE inbox_cache ADD COLUMN imap_uid TEXT",
    ]
    for m in migrations:
        try:
            conn.execute(m); conn.commit()
            logger.info(f"Migracion OK: {m}")
        except Exception:
            pass
    conn.close()
    logger.info("DB lista")

# ─────────────────────────────────────────────
# AUTH — token simple HMAC
# ─────────────────────────────────────────────
def make_token() -> str:
    raw = secrets.token_hex(32)
    sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"

def verify_token(token: str) -> bool:
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return False
        conn = get_db()
        row = conn.execute(
            "SELECT expires_at FROM sessions WHERE token=? AND expires_at>?",
            (token, int(time.time()))
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False

async def require_auth(request: Request):
    token = request.cookies.get("session") or request.headers.get("X-Session-Token", "")
    if not token or not verify_token(token):
        raise HTTPException(401, "No autorizado")
    return token

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    conn.commit(); conn.close()

# ─────────────────────────────────────────────
# MARKDOWN → HTML
# ─────────────────────────────────────────────
def md_to_html(text: str) -> str:
    text = text.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    text = re.sub(r'^### (.+)$', r'<h3 style="margin:16px 0 8px;font-size:1.1em">\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$',  r'<h2 style="margin:20px 0 10px;font-size:1.3em">\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$',   r'<h1 style="margin:24px 0 12px;font-size:1.5em">\1</h1>', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*',     r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*',         r'<em>\1</em>', text)
    text = re.sub(r'__(.+?)__',         r'<strong>\1</strong>', text)
    text = re.sub(r'_(.+?)_',           r'<em>\1</em>', text)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2" style="color:LINKCOLOR;text-decoration:underline">\1</a>', text)
    lines = text.split('\n'); result, in_ul = [], False
    for line in lines:
        if re.match(r'^[-*•] (.+)', line):
            if not in_ul: result.append('<ul style="margin:10px 0;padding-left:24px">'); in_ul=True
            result.append(f'<li style="margin:5px 0">{re.sub(r"^[-*•] ","",line)}</li>')
        else:
            if in_ul: result.append('</ul>'); in_ul=False
            result.append(line)
    if in_ul: result.append('</ul>')
    text = '\n'.join(result)
    paragraphs = re.split(r'\n\n+', text)
    wrapped = []
    for p in paragraphs:
        p = p.strip()
        if not p: continue
        if p.startswith('<h') or p.startswith('<ul') or p.startswith('<li'):
            wrapped.append(p)
        else:
            wrapped.append(f'<p style="margin:0 0 14px;line-height:1.7">{p.replace(chr(10),"<br>")}</p>')
    return '\n'.join(wrapped)

# ─────────────────────────────────────────────
# HTML EMAIL BUILDER
# ─────────────────────────────────────────────
def build_html_email(body_text: str, style_cfg: dict = None) -> str:
    s = style_cfg or {}
    primary   = s.get("primary_color",  "#7ec850")
    bg        = s.get("bg_color",       "#ffffff")
    text_col  = s.get("text_color",     "#1a1a1a")
    font      = s.get("font_family",    "Georgia,'Times New Roman',serif")
    fsize     = s.get("font_size",      "16px")
    link_col  = s.get("link_color",     "#2563eb")
    header_bg = s.get("header_bg",      "#0c0f0a")
    header_fc = s.get("header_color",   "#7ec850")
    sname     = s.get("sender_name",    cfg()["sender_name"])
    sig_html  = s.get("signature_html", get_setting("signature_html",""))
    body_html = md_to_html(body_text).replace("LINKCOLOR", link_col)
    sig_block = f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-top:28px;border-top:2px solid {primary};padding-top:18px">\<td style="font-size:13px;color:#555;font-family:{font}">{sig_html}</td>\</table>' if sig_html else ""
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:{font}">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0">\<td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:{bg};border-radius:10px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08)">
\<td style="background:{header_bg};padding:22px 32px"><span style="font-family:Georgia,serif;font-size:20px;font-weight:600;color:{header_fc};letter-spacing:-0.5px">{sname}</span></td>
\<td style="padding:32px 36px 24px;font-size:{fsize};color:{text_col};line-height:1.75;font-family:{font}">{body_html}{sig_block}</td>
\<td style="background:#f8f8f8;border-top:1px solid #e8e8e8;padding:14px 36px"><p style="margin:0;font-size:12px;color:#999;font-family:Arial,sans-serif">Enviado desde <strong>{sname}</strong>.</p></td>
</table></td></table></body></html>"""

# ─────────────────────────────────────────────
# SMTP SEND
# ─────────────────────────────────────────────
def send_smtp(to: str, subject: str, body_plain: str, body_html: str, reply_to_mid: str = None):
    c = cfg()
    msg = MIMEMultipart("alternative")
    sender = f"{c['sender_name']} <{c['smtp_user']}>" if c['sender_name'] else c['smtp_user']
    msg["From"] = sender; msg["To"] = to; msg["Subject"] = subject
    if reply_to_mid:
        msg["In-Reply-To"] = reply_to_mid; msg["References"] = reply_to_mid
    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html,  "html",  "utf-8"))
    m = c["smtp_method"]
    if m in ("smtp_ssl", "gmail"):
        host = "smtp.gmail.com" if m == "gmail" else c["smtp_host"]
        with smtplib.SMTP_SSL(host, c["smtp_port"], timeout=20) as s:
            s.login(c["smtp_user"], c["smtp_pass"]); s.sendmail(c["smtp_user"], to, msg.as_string())
    elif m == "outlook":
        with smtplib.SMTP("smtp.office365.com", 587, timeout=20) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(c["smtp_user"], c["smtp_pass"]); s.sendmail(c["smtp_user"], to, msg.as_string())
    else:
        with smtplib.SMTP(c["smtp_host"], c["smtp_port"], timeout=20) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(c["smtp_user"], c["smtp_pass"]); s.sendmail(c["smtp_user"], to, msg.as_string())

# ─────────────────────────────────────────────
# IMAP SYNC (delete-aware)
# ─────────────────────────────────────────────
def decode_str(s):
    if not s: return ""
    parts = decode_header(s); result = []
    for part, enc in parts:
        result.append(part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else str(part))
    return "".join(result)

def fetch_inbox_sync(limit=60):
    """
    Sincroniza bandeja con IMAP:
    - Agrega mensajes nuevos
    - Elimina de DB los que ya no existen en IMAP
    - Preserva ai_suggestion y replied si el mensaje sigue existiendo
    """
    c = cfg()
    if not c["imap_host"] or not c["imap_user"]: return {"added":0,"deleted":0,"error":"IMAP no configurado"}
    try:
        mail = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"])
        mail.login(c["imap_user"], c["imap_pass"])
        mail.select("INBOX")

        # Obtener todos los UID actuales del servidor
        _, uid_data = mail.uid("SEARCH", None, "ALL")
        server_uids = set(uid_data[0].decode().split()) if uid_data[0] else set()

        conn = get_db()

        # Obtener message_ids actuales en DB con su uid
        db_rows = conn.execute("SELECT message_id, imap_uid FROM inbox_cache").fetchall()
        db_by_uid = {r["imap_uid"]: r["message_id"] for r in db_rows if r["imap_uid"]}
        db_message_ids = {r["message_id"] for r in db_rows}

        # Detectar UIDs eliminados del servidor y borrarlos de DB
        deleted_uids = set(db_by_uid.keys()) - server_uids
        deleted_count = 0
        for uid in deleted_uids:
            mid = db_by_uid[uid]
            conn.execute("DELETE FROM inbox_cache WHERE message_id=?", (mid,))
            deleted_count += 1
        if deleted_count:
            conn.commit()
            logger.info(f"IMAP sync: {deleted_count} mensajes eliminados de DB")

        # Fetch de los ultimos N mensajes del servidor
        uids_to_fetch = list(server_uids)[-limit:]
        added_count = 0

        for uid in reversed(uids_to_fetch):
            _, msg_data = mail.uid("FETCH", uid, "(RFC822)")
            if not msg_data or not msg_data[0]: continue
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            mid   = msg.get("Message-ID","").strip()
            if not mid: mid = f"uid-{uid}"

            # Ya existe en DB — skip
            if mid in db_message_ids: continue

            subj  = decode_str(msg.get("Subject",""))
            from_ = decode_str(msg.get("From",""))
            date_ = msg.get("Date","")
            from_email, from_name = "", ""
            if "<" in from_:
                pts = from_.split("<")
                from_name  = pts[0].strip().strip('"')
                from_email = pts[1].replace(">","").strip()
            else:
                from_email = from_.strip()
                from_name  = from_email.split("@")[0]
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        try: body = part.get_payload(decode=True).decode("utf-8", errors="replace"); break
                        except: pass
            else:
                try: body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
                except: body = ""
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO inbox_cache (message_id,imap_uid,from_email,from_name,subject,body,date) VALUES (?,?,?,?,?,?,?)",
                    (mid, uid, from_email, from_name, subj, body[:3000], date_))
                conn.commit()
                added_count += 1
            except Exception as e:
                logger.error(f"Error insertando msg {mid}: {e}")

        conn.close()
        mail.logout()
        logger.info(f"IMAP sync: +{added_count} nuevos, -{deleted_count} eliminados")
        return {"added": added_count, "deleted": deleted_count, "total": len(server_uids)}
    except Exception as e:
        logger.error(f"IMAP sync error: {e}")
        return {"added":0,"deleted":0,"error":str(e)}

# ─────────────────────────────────────────────
# APP LIFESPAN
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    init_db()
    yield

app = FastAPI(title="Asistente Ejecutivo API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   allow_credentials=True)

# ─────────────────────────────────────────────
# BUSCAR EL FRONTEND EN MÚLTIPLES UBICACIONES
# ─────────────────────────────────────────────
def find_index_html():
    """Busca index.html en múltiples ubicaciones posibles"""
    posibles_ubicaciones = [
        os.path.join(BASE, "static", "index.html"),
        os.path.join(BASE, "index.html"),
        os.path.join(os.path.dirname(BASE), "static", "index.html"),
        "/opt/render/project/src/static/index.html",  # Ruta típica en Render
        "/app/static/index.html",  # Otra ruta común
    ]
    
    for ubicacion in posibles_ubicaciones:
        if os.path.exists(ubicacion):
            logger.info(f"Frontend encontrado en: {ubicacion}")
            return ubicacion
    
    logger.warning("No se encontró index.html en ninguna ubicación")
    return None

INDEX_PATH = find_index_html()

# Crear directorio static si no existe
static_dir = os.path.join(BASE, "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
    logger.info(f"Creado directorio static: {static_dir}")

# Si el archivo index.html está en la raíz, copiarlo a static/
index_root = os.path.join(BASE, "index.html")
if os.path.exists(index_root) and not os.path.exists(os.path.join(static_dir, "index.html")):
    import shutil
    shutil.copy2(index_root, os.path.join(static_dir, "index.html"))
    logger.info(f"Copiado {index_root} a {static_dir}/")
    INDEX_PATH = os.path.join(static_dir, "index.html")

# Montar archivos estáticos si el directorio existe
if os.path.exists(static_dir) and os.listdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Directorio static montado: {static_dir}")

# Lista de rutas de API que NO deben ser servidas como frontend
API_PATHS = ["auth", "contacts", "inbox", "send-email", "settings", "context", 
             "preview-email", "logs", "memory", "supervision", "stats", 
             "smtp-test", "config", "api"]

# ─────────────────────────────────────────────
# ROUTES — AUTH (sin autenticación para login)
# ─────────────────────────────────────────────
@app.post("/auth/login")
async def login(request: Request):
    data = await request.json()
    user = data.get("username","").strip()
    pw   = data.get("password","")
    if user != APP_USER or pw != APP_PASSWORD:
        raise HTTPException(401, "Credenciales incorrectas")
    token = make_token()
    now   = int(time.time())
    conn  = get_db()
    # limpiar sesiones expiradas
    conn.execute("DELETE FROM sessions WHERE expires_at<?", (now,))
    conn.execute("INSERT INTO sessions (token,created_at,expires_at) VALUES (?,?,?)",
                 (token, now, now + TOKEN_TTL))
    conn.commit(); conn.close()
    resp = JSONResponse({"success": True, "token": token})
    resp.set_cookie("session", token, httponly=True, samesite="lax",
                    max_age=TOKEN_TTL, secure=False)
    return resp

@app.post("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get("session","")
    if token:
        conn = get_db()
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit(); conn.close()
    resp = JSONResponse({"success": True})
    resp.delete_cookie("session")
    return resp

@app.get("/auth/check")
async def auth_check(token: str = Depends(require_auth)):
    return {"ok": True, "user": APP_USER}

# ─────────────────────────────────────────────
# ROUTES — CONFIG/STATUS (protegidas)
# ─────────────────────────────────────────────
@app.get("/api/status")
def root(_: str = Depends(require_auth)):
    c = cfg()
    return {"status": "Asistente Ejecutivo API", "smtp_user": c["smtp_user"] or "NO CONFIG", "imap_host": c["imap_host"] or "NO CONFIG"}

@app.get("/config")
def get_config(_: str = Depends(require_auth)):
    c = cfg()
    return {
        "smtp_method": c["smtp_method"], "smtp_host": c["smtp_host"],
        "smtp_port": c["smtp_port"],     "smtp_user": c["smtp_user"],
        "sender_name": c["sender_name"], "smtp_pass_set": bool(c["smtp_pass"]),
        "imap_host": c["imap_host"],     "imap_port": c["imap_port"],
        "imap_user": c["imap_user"],     "imap_pass_set": bool(c["imap_pass"]),
    }

# ─────────────────────────────────────────────
# ROUTES — SETTINGS
# ─────────────────────────────────────────────
@app.get("/settings")
def get_all_settings(_: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT key,value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}

@app.post("/settings")
async def save_settings(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    for k, v in data.items():
        set_setting(k, str(v))
    return {"success": True}

@app.get("/context")
def get_context(_: str = Depends(require_auth)):
    return {"entity": get_setting("ctx_entity"), "mission": get_setting("ctx_mission"), "extra": get_setting("ctx_extra")}

@app.post("/context")
async def save_context(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    for k in ("entity","mission","extra"):
        if k in data: set_setting("ctx_"+k, data[k])
    return {"success": True}

@app.post("/preview-email")
async def preview_email(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    style = {
        "primary_color":  get_setting("style_primary_color","#7ec850"),
        "bg_color":       get_setting("style_bg_color","#ffffff"),
        "text_color":     get_setting("style_text_color","#1a1a1a"),
        "font_family":    get_setting("style_font_family","Georgia,'Times New Roman',serif"),
        "font_size":      get_setting("style_font_size","16px"),
        "link_color":     get_setting("style_link_color","#2563eb"),
        "header_bg":      get_setting("style_header_bg","#0c0f0a"),
        "header_color":   get_setting("style_header_color","#7ec850"),
        "signature_html": get_setting("signature_html",""),
    }
    for k in style: style[k] = data.get(k, style[k])
    style["sender_name"] = data.get("sender_name", cfg()["sender_name"])
    return {"html": build_html_email(data.get("body",""), style)}

# ─────────────────────────────────────────────
# ROUTES — CONTACTS
# ─────────────────────────────────────────────
@app.get("/contacts")
def list_contacts(_: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM contacts ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/contacts")
async def create_contact(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    if not data.get("name") or not data.get("email"):
        raise HTTPException(400, "name y email son obligatorios")
    conn = get_db()
    try:
        conn.execute("INSERT INTO contacts (name,email,company,role,phone,context,tags) VALUES (?,?,?,?,?,?,?)",
            (data["name"],data["email"],data.get("company",""),data.get("role",""),
             data.get("phone",""),data.get("context",""),data.get("tags","")))
        conn.commit()
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO memory (type,entity,content) VALUES (?,?,?)",
            ("contact_added",data["email"],f"Contacto: {data['name']} - {data.get('company','')}"))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Email ya existe")
    finally:
        conn.close()
    return {"success": True, "id": cid}

@app.put("/contacts/{cid}")
async def update_contact(cid: int, request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    conn = get_db()
    conn.execute("UPDATE contacts SET name=?,company=?,role=?,phone=?,context=?,tags=?,updated_at=datetime('now') WHERE id=?",
        (data.get("name"),data.get("company",""),data.get("role",""),
         data.get("phone",""),data.get("context",""),data.get("tags",""),cid))
    conn.commit(); conn.close()
    return {"success": True}

@app.delete("/contacts/{cid}")
def delete_contact(cid: int, _: str = Depends(require_auth)):
    conn = get_db()
    conn.execute("DELETE FROM contacts WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return {"success": True}

# ─────────────────────────────────────────────
# ROUTES — SEND (simple + multiple)
# ─────────────────────────────────────────────
def _build_style():
    c = cfg()
    return {
        "primary_color":  get_setting("style_primary_color","#7ec850"),
        "bg_color":       get_setting("style_bg_color","#ffffff"),
        "text_color":     get_setting("style_text_color","#1a1a1a"),
        "font_family":    get_setting("style_font_family","Georgia,'Times New Roman',serif"),
        "font_size":      get_setting("style_font_size","16px"),
        "link_color":     get_setting("style_link_color","#2563eb"),
        "header_bg":      get_setting("style_header_bg","#0c0f0a"),
        "header_color":   get_setting("style_header_color","#7ec850"),
        "sender_name":    c["sender_name"],
        "signature_html": get_setting("signature_html",""),
    }

def _log_sent(conn, to, subject, body, body_html, intent, campaign_id):
    row = conn.execute("SELECT name FROM contacts WHERE email=?", (to,)).fetchone()
    cname = row["name"] if row else to.split("@")[0]
    conn.execute(
        "INSERT INTO email_logs (direction,contact_email,contact_name,subject,body,body_html,intent,status,sent_at,campaign_id) VALUES (?,?,?,?,?,?,?,?,datetime('now'),?)",
        ("out",to,cname,subject,body,body_html,intent,"sent",campaign_id))
    conn.execute("INSERT INTO memory (type,entity,content,importance) VALUES (?,?,?,?)",
        ("email_sent",to,f"Email enviado a {cname}: {subject}",2))

@app.post("/send-email")
async def send_email(request: Request, _: str = Depends(require_auth)):
    """Envia a uno o multiples destinatarios."""
    data    = await request.json()
    # Acepta "to" (string) o "recipients" (lista)
    recipients_raw = data.get("recipients") or ([data.get("to","")] if data.get("to") else [])
    recipients = [r.strip() for r in recipients_raw if r.strip()]
    subject     = data.get("subject","").strip()
    body        = data.get("body","").strip()
    intent      = data.get("intent","general")
    campaign_id = data.get("campaign_id")
    reply_to    = data.get("reply_to")

    if not recipients: raise HTTPException(400, "Al menos un destinatario es requerido")
    if not subject:    raise HTTPException(400, "subject es obligatorio")
    if not body:       raise HTTPException(400, "body es obligatorio")

    c = cfg()
    if not c["smtp_user"] or not c["smtp_pass"]:
        raise HTTPException(500, "SMTP no configurado — verifica variables de entorno en Render")

    style_cfg = _build_style()
    body_html = build_html_email(body, style_cfg)

    results = []
    conn = get_db()
    for to in recipients:
        try:
            send_smtp(to, subject, body, body_html, reply_to)
            _log_sent(conn, to, subject, body, body_html, intent, campaign_id)
            results.append({"to": to, "ok": True})
            logger.info(f"Email enviado a {to}")
        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"Auth error -> {to}: {e}")
            results.append({"to": to, "ok": False, "error": f"Error de autenticacion: {e}"})
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error -> {to}: {e}")
            results.append({"to": to, "ok": False, "error": f"Error SMTP: {e}"})
        except OSError as e:
            logger.error(f"Network error -> {to}: {e}")
            results.append({"to": to, "ok": False, "error": f"Error de red: {e}"})
        except Exception as e:
            logger.error(f"Error inesperado -> {to}: {type(e).__name__}: {e}")
            results.append({"to": to, "ok": False, "error": f"{type(e).__name__}: {e}"})
    try:
        conn.commit()
    except Exception as e:
        logger.error(f"Error log DB: {e}")
    finally:
        conn.close()

    sent_ok  = [r for r in results if r["ok"]]
    sent_err = [r for r in results if not r["ok"]]
    if not sent_ok and sent_err:
        raise HTTPException(500, sent_err[0]["error"])
    return {"success": True, "sent": len(sent_ok), "failed": len(sent_err), "results": results}

@app.get("/smtp-test")
async def smtp_test(_: str = Depends(require_auth)):
    c = cfg()
    if not c["smtp_user"] or not c["smtp_pass"]:
        raise HTTPException(500, "SMTP no configurado")
    try:
        m = c["smtp_method"]
        if m in ("smtp_ssl","gmail"):
            host = "smtp.gmail.com" if m == "gmail" else c["smtp_host"]
            with smtplib.SMTP_SSL(host, c["smtp_port"], timeout=10) as s:
                s.login(c["smtp_user"], c["smtp_pass"])
        else:
            host = "smtp.office365.com" if m == "outlook" else c["smtp_host"]
            with smtplib.SMTP(host, c["smtp_port"], timeout=10) as s:
                s.ehlo(); s.starttls(); s.ehlo(); s.login(c["smtp_user"], c["smtp_pass"])
        return {"ok": True, "method": m, "host": c["smtp_host"], "user": c["smtp_user"]}
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")

# ─────────────────────────────────────────────
# ROUTES — INBOX
# ─────────────────────────────────────────────
@app.get("/inbox")
def get_inbox(refresh: bool = False, _: str = Depends(require_auth)):
    result = None
    if refresh:
        result = fetch_inbox_sync(60)
    conn = get_db()
    rows = conn.execute("SELECT * FROM inbox_cache ORDER BY date DESC LIMIT 80").fetchall()
    conn.close()
    resp = [dict(r) for r in rows]
    if result: return {"messages": resp, "sync": result}
    return {"messages": resp, "sync": None}

@app.post("/inbox/mark-replied")
async def mark_replied(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    conn = get_db()
    conn.execute("UPDATE inbox_cache SET replied=1 WHERE message_id=?", (data.get("message_id"),))
    conn.commit(); conn.close()
    return {"success": True}

@app.post("/inbox/save-suggestion")
async def save_suggestion(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    conn = get_db()
    conn.execute("UPDATE inbox_cache SET ai_suggestion=? WHERE message_id=?",
        (data.get("suggestion",""), data.get("message_id")))
    conn.commit(); conn.close()
    return {"success": True}

# ─────────────────────────────────────────────
# ROUTES — LOGS
# ─────────────────────────────────────────────
@app.get("/logs")
def get_logs(limit: int = 100, _: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM email_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/logs/{log_id}")
def get_log(log_id: int, _: str = Depends(require_auth)):
    conn = get_db()
    row = conn.execute("SELECT * FROM email_logs WHERE id=?", (log_id,)).fetchone()
    conn.close()
    if not row: raise HTTPException(404, "Log no encontrado")
    return dict(row)

# ─────────────────────────────────────────────
# ROUTES — MEMORY
# ─────────────────────────────────────────────
@app.get("/memory")
def get_memory(limit: int = 100, _: str = Depends(require_auth)):
    conn = get_db()
    rows = conn.execute("SELECT * FROM memory ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/memory")
async def add_memory(request: Request, _: str = Depends(require_auth)):
    data = await request.json()
    conn = get_db()
    conn.execute("INSERT INTO memory (type,entity,content,importance) VALUES (?,?,?,?)",
        (data.get("type","manual"),data.get("entity",""),data.get("content",""),data.get("importance",1)))
    conn.commit(); conn.close()
    return {"success": True}

# ─────────────────────────────────────────────
# ROUTES — SUPERVISION
# ─────────────────────────────────────────────
@app.get("/supervision")
def get_supervision(_: str = Depends(require_auth)):
    conn = get_db()
    sent = conn.execute("""SELECT l.*, c.company FROM email_logs l
        LEFT JOIN contacts c ON l.contact_email=c.email
        WHERE l.direction='out' ORDER BY l.sent_at DESC LIMIT 100""").fetchall()
    replied_set = {r["from_email"] for r in conn.execute("SELECT from_email FROM inbox_cache WHERE replied=1").fetchall()}
    result = []
    for row in sent:
        d = dict(row)
        try:
            dt  = datetime.fromisoformat(d.get("sent_at",""))
            hrs = round((datetime.utcnow()-dt).total_seconds()/3600, 1)
        except Exception: hrs = None
        d["hours_since_sent"] = hrs
        d["has_reply"] = d["contact_email"] in replied_set
        result.append(d)
    conn.close()
    return result

# ─────────────────────────────────────────────
# ROUTES — STATS
# ─────────────────────────────────────────────
@app.get("/stats")
def get_stats(_: str = Depends(require_auth)):
    conn = get_db()
    r = {
        "contacts": conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0],
        "sent":     conn.execute("SELECT COUNT(*) FROM email_logs WHERE direction='out'").fetchone()[0],
        "inbox":    conn.execute("SELECT COUNT(*) FROM inbox_cache").fetchone()[0],
        "replied":  conn.execute("SELECT COUNT(*) FROM inbox_cache WHERE replied=1").fetchone()[0],
        "memory":   conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0],
    }
    conn.close()
    return r

# ─────────────────────────────────────────────
# SERVIR FRONTEND (sin autenticación)
# ─────────────────────────────────────────────
@app.get("/")
async def serve_frontend():
    """Sirve el frontend SPA sin autenticación"""
    if INDEX_PATH and os.path.exists(INDEX_PATH):
        return FileResponse(INDEX_PATH)
    
    # Intentar servir desde la raíz como fallback
    root_index = os.path.join(BASE, "index.html")
    if os.path.exists(root_index):
        return FileResponse(root_index)
    
    logger.error(f"No se pudo encontrar index.html. INDEX_PATH={INDEX_PATH}, BASE={BASE}")
    return JSONResponse({"error": "Frontend no encontrado"}, status_code=404)

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Sirve el frontend SPA para cualquier ruta no-API"""
    # Excluir rutas de API explícitamente
    if full_path.startswith(tuple(API_PATHS)) or full_path in API_PATHS:
        raise HTTPException(404, "Not found")
    
    # Excluir también rutas con extensión de archivo estático
    if any(full_path.endswith(ext) for ext in ['.js', '.css', '.png', '.jpg', '.svg', '.ico', '.json']):
        raise HTTPException(404, "Not found")
    
    if INDEX_PATH and os.path.exists(INDEX_PATH):
        return FileResponse(INDEX_PATH)
    
    # Intentar servir desde la raíz como fallback
    root_index = os.path.join(BASE, "index.html")
    if os.path.exists(root_index):
        return FileResponse(root_index)
    
    return JSONResponse({"error": "Frontend no encontrado"}, status_code=404)

if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("api:app", host="0.0.0.0", port=port)
