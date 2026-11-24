"""
Regras de irrigação automática (R8).

Avalia, a cada ciclo, se a umidade_solo de cada estação ficou abaixo do
limiar configurado na janela de tempo. Quando isso acontece, abre o alerta
`solo_seco` e age conforme o que a estação declarou ter (campo "recursos"):

  - COM bomba: dispara `bomba on` (duracao_s da regra).
  - SEM bomba: não há como irrigar, então avisa uma pessoa para regar:
    `led on` (aceso até o próximo aviso, máx. 60 s) e `buzzer on` (bipe de
    duracao_s). Repete a cada intervalo_min_s enquanto o solo seguir seco e
    desliga LED/buzzer quando o solo volta ao limiar.

Respeitados:
  - intervalo_min_s  : tempo mínimo entre dois disparos automáticos. Conta
                       qualquer comando da regra, inclusive os rejeitados pelo
                       firmware: sem isso, um "intertravamento_intervalo" (a
                       placa acabou de irrigar sozinha) fazia a regra reenviar
                       o comando a cada REGRAS_INTERVALO_S.
  - modo seguro      : nunca dispara se dispositivo estiver em modo 'seguro'
  - modo manual      : uma pessoa mandou comando há menos de 60 s; a regra
                       não passa por cima (o firmware também pausa a regra local)
  - bomba já ligada  : o último estado publicado diz bomba "on"
  - dispositivo offline: não dispara se o broker não enxergar o dispositivo
  - solo recuperado  : a média da janela ainda está baixa, mas a última leitura
                       já passou do limiar (a bomba acabou de agir) → não irriga
                       de novo, evitando encharcar o vaso
"""

import logging
from sqlalchemy import text

from . import alertas, comandos, contrato, db, notify
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


def _ultima_leitura(conn, dispositivo_id: str, grandeza: str, janela_min: int) -> float | None:
    """Valor mais recente da grandeza dentro da janela, ou None."""
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
    """Segundos desde o último comando "on" da regra (bomba, ou LED/buzzer na
    estação sem bomba), em qualquer estado, ou None se nunca houve.

    Rejeitados e expirados também contam: o intervalo mínimo é o tempo de
    espera entre TENTATIVAS, não só entre irrigações bem-sucedidas."""
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
    """Ponto de entrada chamado pelo loop em main.py a cada REGRAS_INTERVALO_S."""
    with db.transacao() as conn:
        regras = db.linhas(
            conn,
            """
            SELECT r.dispositivo_id, r.grandeza, r.limiar, r.janela_min,
                   r.intervalo_min_s, r.duracao_s,
                   d.modo, d.online, d.recursos, d.ultimo_estado ->> 'bomba' AS bomba
            FROM regras r
            JOIN dispositivos d ON d.id = r.dispositivo_id
            WHERE r.ativa = true
            """,
        )

    for regra in regras:
        _avaliar_regra(ponte, regra)


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
        ultima = _ultima_leitura(conn, dispositivo_id, regra["grandeza"], regra["janela_min"])

    if media is None:
        log.debug("%s sem leituras na janela, regra ignorada", dispositivo_id)
        return

    if media >= regra["limiar"]:
        # Solo úmido o suficiente: se houver alerta aberto, resolve
        with db.transacao() as conn:
            resolvido = alertas.resolver(conn, dispositivo_id, "solo_seco")
        if resolvido:
            log.info("%s solo recuperado (media=%.1f >= limiar=%.1f)", dispositivo_id, media, regra["limiar"])
            if not tem_bomba:
                # alguém regou: apaga o aviso na hora em vez de esperar o tempo acabar
                for alvo in ("led", "buzzer"):
                    if alvo in atuadores:
                        _comando(ponte, dispositivo_id, alvo, "off")
        return

    # Média baixa, mas a leitura mais nova já recuperou: a bomba acabou de agir
    # (ou alguém regou) e a média ainda carrega as leituras secas de antes. Esperar.
    if ultima is not None and ultima >= regra["limiar"]:
        log.debug("%s media=%.1f baixa mas ultima=%.1f ja recuperou", dispositivo_id, media, ultima)
        return

    # Pessoa no controle (comando manual há menos de 60 s) ou bomba já ligada
    if regra["modo"] == "manual":
        log.debug("%s em modo manual, regra pausada", dispositivo_id)
        return
    if tem_bomba and regra["bomba"] == "on":
        log.debug("%s bomba ja ligada, regra aguarda", dispositivo_id)
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

    if tem_bomba:
        log.info(
            "%s solo seco (media=%.1f < limiar=%.1f), disparando bomba %ds",
            dispositivo_id, media, regra["limiar"], regra["duracao_s"],
        )
        if not _comando(ponte, dispositivo_id, "bomba", "on", regra["duracao_s"]):
            return
        acao_tomada = "A bomba foi acionada pela regra."
        mensagem = f"{dispositivo_id} umidade_solo abaixo de {regra['limiar']:g}%"
    else:
        # Sem bomba: LED fica aceso até o próximo aviso (teto de 60 s do contrato) e o
        # buzzer dá um bipe de duracao_s, para não apitar sem parar.
        avisos = [(a, d) for a, d in (("led", min(60, regra["intervalo_min_s"])), ("buzzer", regra["duracao_s"]))
                  if a in atuadores]
        log.info(
            "%s solo seco (media=%.1f < limiar=%.1f) e SEM bomba: avisando por %s",
            dispositivo_id, media, regra["limiar"], " + ".join(a for a, _ in avisos) or "alerta/e-mail",
        )
        enviados = [a for a, d in avisos if _comando(ponte, dispositivo_id, a, "on", d)]
        if avisos and not enviados:
            return
        acao_tomada = "A estacao nao tem bomba: regue manualmente. LED e buzzer foram acionados."
        mensagem = f"{dispositivo_id} umidade_solo abaixo de {regra['limiar']:g}% e sem bomba: regar manualmente"

    # Abre alerta solo_seco (idempotente — não duplica). Só o alerta NOVO manda e-mail.
    with db.transacao() as conn:
        novo = alertas.abrir(conn, dispositivo_id, "solo_seco", mensagem)
    if novo:
        notify.agendar(
            dispositivo_id, "solo_seco", f"[MACP] {dispositivo_id} solo seco",
            f"A umidade do solo da estacao {dispositivo_id} ficou abaixo de {regra['limiar']:g}% "
            f"(media {media:.1f}% em {regra['janela_min']} min). {acao_tomada}",
        )
