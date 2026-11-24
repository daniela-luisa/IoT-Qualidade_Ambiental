from sqlalchemy import text
from sqlalchemy.engine import Connection

TIPOS = {"dispositivo_offline", "solo_seco", "reservatorio_vazio"}


def abrir(conn: Connection, dispositivo_id: str, tipo: str, mensagem: str = "") -> bool:
    if tipo not in TIPOS:
        raise ValueError(f"tipo de alerta desconhecido: {tipo}")
    novo = conn.execute(
        text(
            """
            INSERT INTO alertas (dispositivo_id, tipo, mensagem)
            VALUES (:d, :t, :m)
            ON CONFLICT (dispositivo_id, tipo) WHERE resolvido_em IS NULL DO NOTHING
            RETURNING id
            """
        ),
        {"d": dispositivo_id, "t": tipo, "m": mensagem or None},
    ).first()
    return novo is not None


def resolver(conn: Connection, dispositivo_id: str, tipo: str) -> bool:
    resultado = conn.execute(
        text(
            """
            UPDATE alertas SET resolvido_em = now()
            WHERE dispositivo_id = :d AND tipo = :t AND resolvido_em IS NULL
            """
        ),
        {"d": dispositivo_id, "t": tipo},
    )
    return resultado.rowcount > 0
