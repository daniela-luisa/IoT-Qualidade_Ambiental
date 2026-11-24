import logging
from sqlalchemy import text

from . import alertas, comandos, contrato, db
from .config import config

log = logging.getLogger("macp.regras")


def _media_janela(conn, dispositivo_id: str, grandeza: str, janela_min: int) -> float | None:
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


def _ultima_leitura(conn, dispositivo_id: str, grandeza: str, janela_min: int) -> float | None:
    row = conn.execute(
        text(
            """
            SELECT valor
            FROM leituras
            WHERE dispositivo_id = :d
              AND grandeza = :g
              AND medido_em >= now() - make_interval(mins => :m)
            ORDER BY medido_em DESC
            LIMIT 1
            """
        ),
        {"d": dispositivo_id, "g": grandeza, "m": janela_min},
    ).first()
    return None if row is None else float(row.valor)


def _ultimo_disparo(conn, dispositivo_id: str) -> float | None:
    row = conn.execute(
        text(
            """
            SELECT extract(epoch FROM (now() - max(enviado_em))) AS segundos
            FROM comandos
            WHERE dispositivo_id = :d
              AND origem = 'regra'
              AND acao = 'on'
            """
        ),
        {"d": dispositivo_id},
    ).first()
    if row is None or row.segundos is None:
        return None
    return float(row.segundos)


def avaliar_todas(ponte) -> None:
    with db.transacao() as conn:
        regras = db.linhas(
            conn,
            """
            SELECT r.dispositivo_id, r.grandeza, r.limiar, r.janela_min,
                   r.intervalo_min_s, r.duracao_s,
                   d.modo, d.online, d.recursos, d.ultimo_estado ->> 'bomba' AS bomba,
                   EXISTS (SELECT 1 FROM alertas a WHERE a.dispositivo_id = r.dispositivo_id
                           AND a.tipo = 'reservatorio_vazio' AND a.resolvido_em IS NULL) AS reservatorio_vazio
            FROM regras r
            JOIN dispositivos d ON d.id = r.dispositivo_id
            WHERE r.ativa = true
            """,
        )

    for regra in regras:
        _avaliar_regra(ponte, regra)
    avaliar_reservatorio(ponte)


def _comando(ponte, dispositivo_id: str, alvo: str, acao: str, duracao: int | None = None) -> bool:
    try:
        comandos.criar_comando(ponte, dispositivo_id, alvo, acao, duracao, origem="regra", usuario_id=None)
        return True
    except (comandos.DispositivoOffline, comandos.BrokerIndisponivel, comandos.DispositivoDesconhecido) as exc:
        log.warning("%s nao foi possivel enviar %s %s: %s", dispositivo_id, alvo, acao, exc)
    except contrato.ErroContrato as exc:
        log.error("%s erro de contrato em %s %s: %s", dispositivo_id, alvo, acao, exc)
    return False


def _avaliar_regra(ponte, regra: dict) -> None:
    dispositivo_id = regra["dispositivo_id"]
    atuadores = contrato.recursos_efetivos(regra["recursos"])["atuadores"]
    tem_bomba = "bomba" in atuadores

    if regra["modo"] == "seguro":
        log.debug("%s modo seguro, regra ignorada", dispositivo_id)
        return

    if not regra["online"]:
        log.debug("%s offline, regra ignorada", dispositivo_id)
        return

    with db.transacao() as conn:
        media = _media_janela(conn, dispositivo_id, regra["grandeza"], regra["janela_min"])
        ultima = _ultima_leitura(conn, dispositivo_id, regra["grandeza"], regra["janela_min"])

    if media is None:
        log.debug("%s sem leituras na janela, regra ignorada", dispositivo_id)
        return

    if media >= regra["limiar"]:
        with db.transacao() as conn:
            resolvido = alertas.resolver(conn, dispositivo_id, "solo_seco")
        if resolvido:
            log.info("%s solo recuperado (media=%.1f >= limiar=%.1f)", dispositivo_id, media, regra["limiar"])
            if not tem_bomba:
                for alvo in ("led", "buzzer"):
                    if alvo in atuadores:
                        _comando(ponte, dispositivo_id, alvo, "off")
        return

    if ultima is not None and ultima >= regra["limiar"]:
        log.debug("%s media=%.1f baixa mas ultima=%.1f ja recuperou", dispositivo_id, media, ultima)
        return

    if regra["modo"] == "manual":
        log.debug("%s em modo manual, regra pausada", dispositivo_id)
        return
    if tem_bomba and regra["bomba"] == "on":
        log.debug("%s bomba ja ligada, regra aguarda", dispositivo_id)
        return
    if tem_bomba and regra["reservatorio_vazio"]:
        log.debug("%s reservatorio vazio (bomba bloqueada), regra aguarda o reabastecimento", dispositivo_id)
        return

    with db.transacao() as conn:
        segundos_desde = _ultimo_disparo(conn, dispositivo_id)

    if segundos_desde is not None and segundos_desde < regra["intervalo_min_s"]:
        log.debug(
            "%s aguardando intervalo (%.0fs < %ds)",
            dispositivo_id, segundos_desde, regra["intervalo_min_s"],
        )
        return

    if tem_bomba:
        log.info(
            "%s solo seco (media=%.1f < limiar=%.1f), disparando bomba %ds",
            dispositivo_id, media, regra["limiar"], regra["duracao_s"],
        )
        if not _comando(ponte, dispositivo_id, "bomba", "on", regra["duracao_s"]):
            return
        mensagem = f"{dispositivo_id} umidade_solo abaixo de {regra['limiar']:g}%"
    else:
        with db.transacao() as conn:
            silenciado = alarme_silenciado(conn, dispositivo_id, "solo_seco")
        avisos = [(a, d) for a, d in (("led", min(60, regra["intervalo_min_s"])), ("buzzer", regra["duracao_s"]))
                  if a in atuadores and not (a == "buzzer" and silenciado)]
        log.info(
            "%s solo seco (media=%.1f < limiar=%.1f) e SEM bomba: avisando por %s",
            dispositivo_id, media, regra["limiar"], " + ".join(a for a, _ in avisos) or "so alerta",
        )
        enviados = [a for a, d in avisos if _comando(ponte, dispositivo_id, a, "on", d)]
        if avisos and not enviados:
            return
        mensagem = f"{dispositivo_id} umidade_solo abaixo de {regra['limiar']:g}% e sem bomba: regar manualmente"

    with db.transacao() as conn:
        alertas.abrir(conn, dispositivo_id, "solo_seco", mensagem)


RESERVATORIO_IRRIGACOES = 3
RESERVATORIO_SUBIDA_MIN = 5.0
RESERVATORIO_ASSENTAR_S = 60
RESERVATORIO_JANELA_H = 6
ALARME_REPETIR_S = 600
ALARME_BUZZER_S = 3
ALARME_LED_S = 60
CONFIG_REENVIO_S = 60


def alarme_silenciado(conn, dispositivo_id: str, tipo_alerta: str) -> bool:
    return conn.execute(
        text(
            """
            SELECT 1 FROM alertas a
            JOIN eventos e ON e.dispositivo_id = a.dispositivo_id AND e.tipo = 'alarme'
                          AND e.criado_em >= a.aberto_em
            WHERE a.dispositivo_id = :d AND a.tipo = :t AND a.resolvido_em IS NULL
            LIMIT 1
            """
        ),
        {"d": dispositivo_id, "t": tipo_alerta},
    ).first() is not None


def _subida_do_solo(conn, dispositivo_id: str) -> float | None:
    ligadas = db.linhas(
        conn,
        """
        SELECT criado_em FROM eventos
        WHERE dispositivo_id = :d AND tipo = 'bomba' AND detalhe LIKE 'on%'
          AND criado_em >= now() - make_interval(hours => :h)
          AND criado_em > COALESCE((SELECT max(resolvido_em) FROM alertas
                                    WHERE dispositivo_id = :d AND tipo = 'reservatorio_vazio'),
                                   '-infinity'::timestamptz)
        ORDER BY criado_em DESC LIMIT :n
        """,
        d=dispositivo_id, h=RESERVATORIO_JANELA_H, n=RESERVATORIO_IRRIGACOES,
    )
    if len(ligadas) < RESERVATORIO_IRRIGACOES:
        return None
    ultima, primeira = ligadas[0]["criado_em"], ligadas[-1]["criado_em"]
    if (contrato.agora_utc() - ultima).total_seconds() < RESERVATORIO_ASSENTAR_S:
        return None
    antes = conn.execute(
        text(
            """
            SELECT valor FROM leituras
            WHERE dispositivo_id = :d AND grandeza = 'umidade_solo'
              AND medido_em <= :t AND medido_em >= :t - interval '10 minutes'
            ORDER BY medido_em DESC LIMIT 1
            """
        ),
        {"d": dispositivo_id, "t": primeira},
    ).scalar()
    depois = conn.execute(
        text(
            """
            SELECT max(valor) FROM leituras
            WHERE dispositivo_id = :d AND grandeza = 'umidade_solo' AND medido_em > :t
            """
        ),
        {"d": dispositivo_id, "t": ultima},
    ).scalar()
    if antes is None or depois is None:
        return None
    return float(depois) - float(antes)


def _config_recente(conn, dispositivo_id: str) -> bool:
    return conn.execute(
        text(
            """
            SELECT 1 FROM comandos
            WHERE dispositivo_id = :d AND alvo = 'config'
              AND (parametros -> 'bombaBloqueada') IS NOT NULL
              AND enviado_em >= now() - make_interval(secs => :s)
            LIMIT 1
            """
        ),
        {"d": dispositivo_id, "s": CONFIG_REENVIO_S},
    ).first() is not None


def _ultimo_alarme_s(conn, dispositivo_id: str, desde) -> float | None:
    row = conn.execute(
        text(
            """
            SELECT extract(epoch FROM (now() - max(enviado_em))) AS s FROM comandos
            WHERE dispositivo_id = :d AND origem = 'regra' AND alvo = 'buzzer' AND acao = 'on'
              AND enviado_em >= :desde
            """
        ),
        {"d": dispositivo_id, "desde": desde},
    ).first()
    return None if row is None or row.s is None else float(row.s)


def avaliar_reservatorio(ponte) -> None:
    with db.transacao() as conn:
        estacoes = db.linhas(
            conn,
            """
            SELECT id, online, recursos, ultimo_estado ->> 'bombaBloqueada' AS bloqueada
            FROM dispositivos
            """,
        )
    for est in estacoes:
        atuadores = contrato.recursos_efetivos(est["recursos"])["atuadores"]
        if "bomba" not in atuadores or not est["online"]:
            continue
        try:
            _reservatorio(ponte, est, atuadores)
        except Exception:
            log.exception("%s erro avaliando o reservatorio", est["id"])


def _reservatorio(ponte, est: dict, atuadores: list) -> None:
    d = est["id"]
    with db.transacao() as conn:
        alerta = db.linha(
            conn,
            "SELECT aberto_em FROM alertas WHERE dispositivo_id = :d AND tipo = 'reservatorio_vazio' "
            "AND resolvido_em IS NULL",
            d=d,
        )
        if alerta is None:
            subida = _subida_do_solo(conn, d)
            if subida is not None and subida < RESERVATORIO_SUBIDA_MIN:
                msg = (f"{d} a bomba ligou {RESERVATORIO_IRRIGACOES} vezes e a umidade do solo subiu "
                       f"so {max(subida, 0):.1f} pontos: reservatorio vazio, bomba sem puxar ou sensor fora da terra")
                if alertas.abrir(conn, d, "reservatorio_vazio", msg):
                    log.warning("%s reservatorio vazio? (subida %.1f) bomba sera bloqueada", d, subida)
                    alerta = db.linha(
                        conn,
                        "SELECT aberto_em FROM alertas WHERE dispositivo_id = :d AND tipo = 'reservatorio_vazio' "
                        "AND resolvido_em IS NULL",
                        d=d,
                    )
        silenciado = alerta is not None and alarme_silenciado(conn, d, "reservatorio_vazio")
        ultimo_alarme = _ultimo_alarme_s(conn, d, alerta["aberto_em"]) if alerta else None
        config_recente = _config_recente(conn, d)

    if est["bloqueada"] is not None and not config_recente:
        quer = 1 if alerta else 0
        if int(est["bloqueada"]) != quer:
            log.info("%s %s a bomba na placa", d, "bloqueando" if quer else "desbloqueando")
            _comando_config(ponte, d, {"bombaBloqueada": quer})

    if alerta and not silenciado and (ultimo_alarme is None or ultimo_alarme >= ALARME_REPETIR_S):
        if "buzzer" in atuadores:
            _comando(ponte, d, "buzzer", "on", ALARME_BUZZER_S)
        if "led" in atuadores:
            _comando(ponte, d, "led", "on", ALARME_LED_S)


def _comando_config(ponte, dispositivo_id: str, parametros: dict, origem: str = "regra",
                    usuario_id: int | None = None) -> bool:
    try:
        comandos.criar_comando(ponte, dispositivo_id, "config", "set", None, parametros,
                               origem=origem, usuario_id=usuario_id)
        return True
    except (comandos.DispositivoOffline, comandos.BrokerIndisponivel, comandos.DispositivoDesconhecido) as exc:
        log.warning("%s nao foi possivel enviar config %s: %s", dispositivo_id, parametros, exc)
    except contrato.ErroContrato as exc:
        log.error("%s erro de contrato em config %s: %s", dispositivo_id, parametros, exc)
    return False
