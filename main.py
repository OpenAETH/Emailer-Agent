from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os

app = FastAPI(title="Email Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SMTP_HOST = os.getenv("SMTP_HOST", "mail.seudominio.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "seu@dominio.com")
SMTP_PASS = os.getenv("SMTP_PASS", "sua_senha")

class EmailPayload(BaseModel):
    to: str
    subject: str
    body: str

@app.get("/")
def root():
    return {"status": "Email Agent API running"}

@app.post("/send-email")
def send_email(payload: EmailPayload):
    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_USER
        msg["To"] = payload.to
        msg["Subject"] = payload.subject
        msg.attach(MIMEText(payload.body, "plain"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, payload.to, msg.as_string())

        return {"success": True, "message": "Email enviado com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
