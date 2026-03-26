# Asistente Ejecutivo — Deploy Render

## Estructura
```
api.py          Backend FastAPI (sirve API + frontend)
index.html      Frontend SPA
requirements.txt
render.yaml     Config de Render
.env.example    Variables de entorno de referencia
```

## Deploy en Render

1. Sube todo a GitHub
2. En Render: New → Web Service → conecta el repo
3. En Environment Variables configura:
   - APP_USER, APP_PASSWORD
   - SECRET_KEY (genera con: python -c "import secrets; print(secrets.token_hex(32))")
   - SMTP_METHOD, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SENDER_NAME
   - IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASS
4. Build Command: pip install -r requirements.txt && mkdir -p static && cp index.html static/
5. Start Command: python api.py

## IMAP Sync
La bandeja se sincroniza con IMAP usando UIDs:
- Al presionar "Actualizar IMAP" elimina de la DB los mensajes que
  ya no existen en el servidor (borrados desde el cliente de email)
- Agrega solo los mensajes nuevos, preservando ai_suggestion y replied

## Envio Multiple
POST /send-email acepta:
- "to": "email@ejemplo.com"  (un destinatario)
- "recipients": ["a@x.com", "b@x.com"]  (multiples)
Devuelve resultados individuales por destinatario.
