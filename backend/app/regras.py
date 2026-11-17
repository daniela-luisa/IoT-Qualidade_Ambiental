"""
Regras de irrigação automática (R8).

Avalia, a cada ciclo, se a umidade_solo de cada estação ficou abaixo do
limiar configurado na janela de tempo. Quando isso acontece, dispara o
comando `bomba on` via MQTT e abre o alerta `solo_seco`.

Respeitados:
  - intervalo_min_s  : tempo mínimo entre dois disparos automáticos
  - modo seguro      : nunca dispara se dispositivo estiver em modo 'seguro'
  - dispositivo offline: não dispara se o broker não enxergar o dispositivo
"""

import logging
from datetime import timedelta

from sqlalchemy import text

from . import alertas, comandos, contrato, db
from .config import config

log = logging.getLogger("macp.regras")


def _media_janela(conn, dispositivo_id: str, grandeza: str, janela_min: int) -> float | None:
    """Retorna a média da grandeza na janela ou None se não há leituras."""
    row = conn.execute(
        text(
            """
            SELECT avg(valor) AS media
            FROM leituras
            WHERE dispositivo_id = :d
              AND grandeza = :g
              AND medido_em >= now() - make_interval(mins => :m)
            """
        ),
        {"d": dispositivo_id, "g": grandeza, "m": janela_min},
    ).first()
    if row is None or row.media is None:
        return None
    return float(row.media)


def _ultimo_disparo(conn, dispositivo_id: str) -> float | None:
    """Segundos desde o último comando de bomba automático (origem='regra'),
    ou None se nunca houve."""
    row = conn.execute(
        text(
            """
            SELECT extract(epoch FROM (now() - max(enviado_em))) AS segundos
            FROM comandos
            WHERE dispositivo_id = :d
              AND alvo = 'bomba'
              AND origem = 'regra'
              AND estado NOT IN ('rejeitado', 'expirado')
            """
        ),
        {"d": dispositivo_id},
    ).first()
    if row is None or row.segundos is None:
        return None
    return float(row.segundos)


def avaliar_todas(ponte) -> None:
    """Ponto de entrada chamado pelo loop em main.py a cada REGRAS_INTERVALO_S."""
    with db.transacao() as conn:
        regras = db.linhas(
            conn,
            """
            SELECT r.dispositivo_id, r.grandeza, r.limiar, r.janela_min,
                   r.intervalo_min_s, r.duracao_s,
                   d.modo, d.online
            FROM regras r
            JOIN dispositivos d ON d.id = r.dispositivo_id
            WHERE r.ativa = true
            """,
        )

    for regra in regras:
        _avaliar_regra(ponte, regra)


def _avaliar_regra(ponte, regra: dict) -> None:
    dispositivo_id = regra["dispositivo_id"]

    # Modo seguro: nunca irrigar automaticamente
    if regra["modo"] == "seguro":
        log.debug("%s modo seguro, regra ignorada", dispositivo_id)
        return

    # Dispositivo offline: broker não consegue entregar
    if not regra["online"]:
        log.debug("%s offline, regra ignorada", dispositivo_id)
        return

    with db.transacao() as conn:
        media = _media_janela(conn, dispositivo_id, regra["grandeza"], regra["janela_min"])

    if media is None:
        log.debug("%s sem leituras na janela, regra ignorada", dispositivo_id)
        return

    if media >= regra["limiar"]:
        # Solo úmido o suficiente: se houver alerta aberto, resolve
        with db.transacao() as conn:
            resolvido = alertas.resolver(conn, dispositivo_id, "solo_seco")
        if resolvido:
            log.info("%s solo recuperado (media=%.1f >= limiar=%.1f)", dispositivo_id, media, regra["limiar"])
        return

    # Solo abaixo do limiar — verificar intervalo mínimo entre disparos
    with db.transacao() as conn:
        segundos_desde = _ultimo_disparo(conn, dispositivo_id)

    if segundos_desde is not None and segundos_desde < regra["intervalo_min_s"]:
        log.debug(
            "%s aguardando intervalo (%.0fs < %ds)",
            dispositivo_id, segundos_desde, regra["intervalo_min_s"],
        )
        return

    # Dispara a bomba
    log.info(
        "%s solo seco (media=%.1f < limiar=%.1f), disparando bomba %ds",
        dispositivo_id, media, regra["limiar"], regra["duracao_s"],
    )
    try:
        comandos.criar_comando(
            ponte,
            dispositivo_id,
            "bomba",
            "on",
            regra["duracao_s"],
            origem="regra",
            usuario_id=None,
        )
    except (comandos.DispositivoOffline, comandos.BrokerIndisponivel, comandos.DispositivoDesconhecido) as exc:
        log.warning("%s nao foi possivel disparar regra: %s", dispositivo_id, exc)
        return
    except contrato.ErroContrato as exc:
        log.error("%s erro de contrato ao disparar regra: %s", dispositivo_id, exc)
        return

    # Abre alerta solo_seco (idempotente — não duplica)
    with db.transacao() as conn:
        alertas.abrir(conn, dispositivo_id, "solo_seco", f"{dispositivo_id} umidade_solo abaixo de {regra['limiar']}%")
