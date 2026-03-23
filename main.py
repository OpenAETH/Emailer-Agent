from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Email Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SMTP_HOST = os.getenv("SMTP_HOST", "mail.agraound.site")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

@app.get("/")
def root():
    return {
        "status": "Email Agent API running",
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "smtp_user": SMTP_USER if SMTP_USER else "NO CONFIGURADO",
        "smtp_pass_set": "SI" if SMTP_PASS else "NO CONFIGURADO"
    }

@app.post("/send-email")
async def send_email(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalido")

    to = data.get("to", "").strip()
    subject = data.get("subject", "").strip()
    body = data.get("body", "").strip()

    if not to or not subject or not body:
        raise HTTPException(status_code=400, detail="Campos to, subject y body son obligatorios")

    logger.info(f"Enviando a: {to} via {SMTP_HOST}:{SMTP_PORT}")

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Puerto 465 = SSL directo (no STARTTLS)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())

        logger.info("Email enviado exitosamente")
        return {"success": True, "message": "Email enviado con exito!"}

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"Auth error: {e}")
        raise HTTPException(status_code=401, detail=f"Error de autenticacion: {str(e)}")
    except smtplib.SMTPConnectError as e:
        logger.error(f"Connect error: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo conectar al SMTP: {str(e)}")
    except Exception as e:
        logger.error(f"Error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)