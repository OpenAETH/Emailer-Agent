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

SMTP_HOST = os.getenv("SMTP_HOST", "mail.seudominio.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
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

    logger.info(f"Intentando enviar email a: {to}")
    logger.info(f"SMTP: {SMTP_HOST}:{SMTP_PORT} con usuario: {SMTP_USER}")

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.set_debuglevel(1)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())

        logger.info("Email enviado exitosamente")
        return {"success": True, "message": "Email enviado con exito!"}

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"Error de autenticacion: {e}")
        raise HTTPException(status_code=401, detail=f"Error de autenticacion SMTP: {str(e)}")
    except smtplib.SMTPConnectError as e:
        logger.error(f"Error de conexion SMTP: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo conectar al servidor SMTP: {str(e)}")
    except smtplib.SMTPException as e:
        logger.error(f"Error SMTP: {e}")
        raise HTTPException(status_code=500, detail=f"Error SMTP: {str(e)}")
    except Exception as e:
        logger.error(f"Error inesperado: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)