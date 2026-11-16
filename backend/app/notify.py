import logging
import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

from . import db
from .config import config

log = logging.getLogger("macp.notify")

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="email")
sincrono = False


def enviar_smtp(destino: str, assunto: str, corpo: str) -> None:
    msg = EmailMessage()
    msg["From"] = config.smtp_user
    msg["To"] = destino
    msg["Subject"] = assunto
    msg.set_content(corpo)
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(msg)


def _enviar(dispositivo_id: str | None, motivo: str, assunto: str, corpo: str) -> None:
    destino = config.alerta_destino
    enviada, erro = False, None
    if not (config.smtp_host and config.smtp_user and config.smtp_password and destino):
        erro = "smtp_nao_configurado"
    else:
        try:
            enviar_smtp(destino, assunto, corpo)
            enviada = True
        except Exception as e:
            erro = f"{type(e).__name__}: {e}"[:300]
            log.warning("falha ao enviar e-mail: %s", erro)
    try:
        with db.transacao() as conn:
            conn.exec_driver_sql(
                "INSERT INTO notificacoes (dispositivo_id, motivo, destino, assunto, enviada, erro) "
                "VALUES (%(d)s, %(m)s, %(to)s, %(a)s, %(ok)s, %(e)s)",
                {"d": dispositivo_id, "m": motivo, "to": destino or None, "a": assunto, "ok": enviada, "e": erro},
            )
    except Exception:
        log.exception("nao foi possivel registrar a notificacao")


def agendar(dispositivo_id: str | None, motivo: str, assunto: str, corpo: str) -> None:
    if sincrono:
        _enviar(dispositivo_id, motivo, assunto, corpo)
    else:
        _executor.submit(_enviar, dispositivo_id, motivo, assunto, corpo)
