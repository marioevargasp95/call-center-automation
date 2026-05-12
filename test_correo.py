"""
test_correo.py
==============
Prueba la conexión SMTP y envía un correo de prueba.
Ejecutar: python test_correo.py
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Configuración ─────────────────────────────────────────────────────────────
SMTP_SERVER   = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_USER     = "tu_correo@empresa.co"
SMTP_PASSWORD = "TU_SMTP_PASSWORD"
EMAIL_DESTINO = "tu_correo@empresa.co"

# ── Test ──────────────────────────────────────────────────────────────────────
print("Probando conexión SMTP...")
print(f"  Servidor : {SMTP_SERVER}:{SMTP_PORT}")
print(f"  Usuario  : {SMTP_USER}")
print(f"  Destino  : {EMAIL_DESTINO}")
print()

msg = MIMEMultipart("alternative")
msg["Subject"] = "✅ Prueba de conexión — Pipeline Call Center"
msg["From"]    = SMTP_USER
msg["To"]      = EMAIL_DESTINO
msg.attach(MIMEText("<h3>Conexión exitosa.</h3><p>El pipeline puede enviar correos correctamente.</p>", "html", "utf-8"))

try:
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        print("1/4 Conectando al servidor...")
        srv.ehlo()
        print("2/4 Iniciando TLS...")
        srv.starttls()
        srv.ehlo()
        print("3/4 Autenticando...")
        srv.login(SMTP_USER, SMTP_PASSWORD)
        print("4/4 Enviando correo de prueba...")
        srv.sendmail(SMTP_USER, EMAIL_DESTINO, msg.as_bytes())

    print()
    print("✅ ÉXITO — revisa tu bandeja de entrada.")

except smtplib.SMTPAuthenticationError:
    print()
    print("❌ ERROR DE AUTENTICACIÓN")
    print("   Google bloqueó el acceso con contraseña normal.")
    print("   Solución: genera una contraseña de aplicación en:")
    print("   https://myaccount.google.com → Seguridad → Contraseñas de aplicaciones")

except smtplib.SMTPConnectError:
    print()
    print("❌ ERROR DE CONEXIÓN — no se pudo llegar al servidor.")
    print("   Verifica que smtp.gmail.com:587 no esté bloqueado por el firewall.")

except Exception as e:
    print()
    print(f"❌ ERROR: {e}")
