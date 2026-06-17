"""
Service d'envoi d'emails via SMTP (settings.json → "email").

Utilisé pour les emails de réinitialisation de mot de passe
(auth/router.py:forgot_password) et le test de configuration SMTP
(routers/settings_router.py:test_email).

Configuration dans settings.json → "email" :
  {
    "enabled": false,
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_user": "repod@example.com",
    "smtp_password": "secret",
    "from_address": "repod@example.com",
    "to_addresses": "rssi@example.com,admin@example.com",
    "use_tls": true
  }
"""

import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from services.settings import get_settings

logger = logging.getLogger("email_notifications")

# Retry sur erreurs de connexion SMTP transitoires uniquement — jamais sur un
# échec d'authentification (identifiants invalides = retry inutile).
_retry_smtp_transient = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((
        smtplib.SMTPConnectError,
        smtplib.SMTPServerDisconnected,
        ConnectionError,
        TimeoutError,
        OSError,
    )),
)


def _get_email_cfg() -> dict | None:
    """Retourne la config email si activée et complète, sinon None."""
    cfg = get_settings().get("email", {})
    if not cfg.get("enabled"):
        return None
    if not cfg.get("smtp_host") or not cfg.get("to_addresses"):
        logger.warning("[email] Configuration incomplète — smtp_host ou to_addresses manquant")
        return None
    return cfg


def _send_email_to(subject: str, body_html: str, body_text: str, cfg: dict, recipients: list[str]) -> bool:
    """Envoie un email à une liste explicite de destinataires. Retourne True si OK."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[repod] {subject}"
    msg["From"]    = cfg.get("from_address", cfg.get("smtp_user", "repod@localhost"))
    msg["To"]      = ", ".join(recipients)

    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html",  "utf-8"))

    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port", 587))
    user = cfg.get("smtp_user", "")
    pwd  = cfg.get("smtp_password", "")
    tls  = cfg.get("use_tls", True)
    # Port 465 = SSL direct (SMTP_SSL) ; port 587/25 = STARTTLS ou plain
    use_ssl = (port == 465)

    try:
        _smtp_send(host, port, use_ssl, tls, user, pwd, msg, recipients)
        logger.info(f"[email] Envoyé '{subject}' → {recipients}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"[email] Authentification SMTP échouée : {e}")
    except smtplib.SMTPConnectError as e:
        logger.error(f"[email] Connexion SMTP impossible ({host}:{port}) : {e}")
    except Exception as e:
        logger.error(f"[email] Erreur envoi : {e}")
    return False


@_retry_smtp_transient
def _smtp_send(host: str, port: int, use_ssl: bool, tls: bool, user: str, pwd: str,
                msg: MIMEMultipart, recipients: list[str]) -> None:
    """Connexion + envoi SMTP avec retry/backoff sur erreurs réseau transitoires (3 tentatives)."""
    context = ssl.create_default_context()
    if use_ssl:
        cm = smtplib.SMTP_SSL(host, port, timeout=10, context=context)
    else:
        cm = smtplib.SMTP(host, port, timeout=10)
    with cm as server:
        if not use_ssl and tls:
            server.starttls(context=context)
        if user and pwd:
            server.login(user, pwd)
        server.sendmail(msg["From"], recipients, msg.as_string())


def _send_email(
    subject: str,
    body_html: str,
    body_text: str,
    to_override: str | None = None,
) -> bool:
    """
    Envoie un email aux destinataires configurés (ou à to_override). Retourne True si OK.

    to_override : si fourni et non-vide, remplace to_addresses pour cet envoi.
                  Usage : reset password, notifications par utilisateur.
    """
    cfg = _get_email_cfg()
    if not cfg:
        return False

    if to_override is not None:
        stripped = to_override.strip()
        recipients = [stripped] if stripped else []
    else:
        recipients = [r.strip() for r in cfg["to_addresses"].split(",") if r.strip()]

    if not recipients:
        return False
    return _send_email_to(subject, body_html, body_text, cfg, recipients)


def _base_style() -> str:
    return """
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             background: #f8fafc; margin: 0; padding: 20px; }
      .card { background: #fff; border-radius: 12px; border: 1px solid #e2e8f0;
              max-width: 600px; margin: 0 auto; overflow: hidden; }
      .header { background: #1e293b; padding: 20px 24px; }
      .header h1 { color: #fff; margin: 0; font-size: 18px; }
      .header p  { color: #94a3b8; margin: 4px 0 0; font-size: 13px; }
      .body { padding: 24px; }
      .badge { display: inline-block; padding: 2px 10px; border-radius: 99px;
               font-size: 12px; font-weight: 600; }
      .badge-red    { background: #fee2e2; color: #dc2626; }
      .badge-orange { background: #ffedd5; color: #ea580c; }
      .badge-amber  { background: #fef3c7; color: #d97706; }
      .badge-blue   { background: #dbeafe; color: #2563eb; }
      .badge-green  { background: #dcfce7; color: #16a34a; }
      .table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
      .table th { background: #f1f5f9; padding: 8px 12px; text-align: left;
                  font-size: 11px; text-transform: uppercase; color: #64748b; }
      .table td { padding: 10px 12px; border-bottom: 1px solid #f1f5f9; }
      .mono { font-family: monospace; }
      .footer { padding: 16px 24px; background: #f8fafc;
                border-top: 1px solid #e2e8f0; font-size: 12px; color: #94a3b8; }
      .btn { display: inline-block; margin-top: 16px; padding: 10px 20px;
             background: #3b82f6; color: #fff; border-radius: 8px;
             text-decoration: none; font-size: 14px; font-weight: 600; }
    </style>
    """


def send_test_email(to_override: str | None = None) -> dict:
    """Envoie un email de test. Retourne {ok, error}."""
    cfg = get_settings().get("email", {})
    if not cfg.get("enabled"):
        return {"ok": False, "error": "Notifications email désactivées dans les paramètres"}
    if not cfg.get("smtp_host"):
        return {"ok": False, "error": "smtp_host non configuré"}

    html = f"""<!DOCTYPE html><html><head>{_base_style()}</head><body>
    <div class='card'>
      <div class='header'><h1>✅ Test email repod</h1></div>
      <div class='body'>
        <p>Si vous recevez cet email, la configuration SMTP est correcte.</p>
        <p style='color:#64748b;font-size:13px'>
          Serveur : {cfg.get('smtp_host')}:{cfg.get('smtp_port', 587)}<br>
          Envoyé le : {datetime.now().strftime('%d/%m/%Y à %H:%M:%S')}
        </p>
      </div>
      <div class='footer'>repod APT Repository Manager</div>
    </div></body></html>"""
    text = "Test email repod — configuration SMTP OK"

    # Envoi direct sans modifier settings.json
    recipients = [to_override.strip()] if to_override else [
        r.strip() for r in cfg.get("to_addresses", "").split(",") if r.strip()
    ]
    if not recipients:
        return {"ok": False, "error": "Aucun destinataire configuré (to_addresses vide)"}

    ok = _send_email_to("Test de configuration SMTP", html, text, cfg, recipients)
    return {"ok": ok, "error": None if ok else "Échec d'envoi — vérifiez les logs backend"}
