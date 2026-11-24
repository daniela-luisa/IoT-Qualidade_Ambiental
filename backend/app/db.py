import logging
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

log = logging.getLogger("macp.db")

_engine: Engine | None = None


def iniciar(url: str) -> Engine:
    global _engine
    _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return _engine


def engine() -> Engine:
    if _engine is None:
        raise RuntimeError("banco nao iniciado: chame db.iniciar(url) antes")
    return _engine


@contextmanager
def transacao():
    with engine().begin() as conn:
        yield conn


def esperar_banco(tentativas: int = 30, intervalo_s: float = 2.0) -> None:
    for tentativa in range(1, tentativas + 1):
        if banco_ok():
            log.info("banco disponivel")
            return
        log.warning("banco indisponivel (tentativa %d/%d)", tentativa, tentativas)
        time.sleep(intervalo_s)
    raise RuntimeError("banco nao respondeu na partida")


MIGRACOES = (
    "ALTER TABLE dispositivos ADD COLUMN IF NOT EXISTS recursos jsonb",
    "ALTER TABLE alertas DROP CONSTRAINT IF EXISTS alertas_tipo_check",
    "ALTER TABLE alertas ADD CONSTRAINT alertas_tipo_check "
    "CHECK (tipo IN ('dispositivo_offline', 'solo_seco', 'reservatorio_vazio'))",
)


def migrar() -> None:
    with transacao() as conn:
        for sql in MIGRACOES:
            conn.execute(text(sql))


def banco_ok() -> bool:
    try:
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def linhas(conn: Connection, sql: str, **params) -> list[dict]:
    return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def linha(conn: Connection, sql: str, **params) -> dict | None:
    r = conn.execute(text(sql), params).first()
    return dict(r._mapping) if r else None


def registrar_evento(conn: Connection, dispositivo_id: str | None, tipo: str, detalhe: str = "") -> None:
    conn.execute(
        text("INSERT INTO eventos (dispositivo_id, tipo, detalhe) VALUES (:d, :t, :x)"),
        {"d": dispositivo_id, "t": tipo, "x": detalhe[:500] if detalhe else None},
    )
