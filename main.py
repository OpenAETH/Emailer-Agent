from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os

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
    return {"status": "Email Agent API running"}

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
        raise HTTPException(status_code=400, detail="Campos to, subject e body sao obrigatorios")

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, to, msg.as_string())

        return {"success": True, "message": "Email enviado com sucesso!"}
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(status_code=401, detail="Erro de autenticacao SMTP. Verifique usuario e senha.")
    except smtplib.SMTPException as e:
        raise HTTPException(status_code=500, detail=f"Erro SMTP: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
