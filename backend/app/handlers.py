import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import alertas, comandos, contrato, db, notify
from .config import config

log = logging.getLogger("macp.handlers")


def tratar_mensagem(topico: str, payload: bytes) -> None:
    try:
        _tratar(topico, payload)
    except Exception as erro:
        log.exception("erro inesperado tratando %s", topico)
        try:
            with db.transacao() as conn:
                db.registrar_evento(conn, None, "mensagem_invalida", f"{topico}: erro interno {type(erro).__name__}")
        except Exception:
            log.exception("nao foi possivel registrar o evento de erro")


def _separar_topico(topico: str):
    partes = topico.split("/")
    if len(partes) < 3 or partes[0] != config.mqtt_prefixo:
        return None
    device_id, tipo = partes[1], partes[2]
    resto = partes[3] if len(partes) == 4 else None
    if len(partes) > 4 or (tipo == "telemetria") != (resto is not None):
        return None
    return device_id, tipo, resto


def _tratar(topico: str, payload: bytes) -> None:
    partes = _separar_topico(topico)
    if partes is None:
        log.debug("topico ignorado: %s", topico)
        return
    device_id, tipo, grandeza = partes
    if not contrato.device_id_valido(device_id):
        log.debug("deviceId fora do padrao estacao-NN ignorado: %s", device_id)
        return
    if tipo == "comando":
        return

    chegada = contrato.agora_utc()
    pendentes: list[tuple] = []

    with db.transacao() as conn:
        existe = conn.execute(text("SELECT 1 FROM dispositivos WHERE id = :d"), {"d": device_id}).first()
        if not existe:
            db.registrar_evento(conn, None, "mensagem_invalida", f"dispositivo desconhecido {device_id} em {topico}")
            return

        try:
            dados = contrato.ler_json(payload)
            payload_id = dados.get("deviceId")
            if payload_id is not None and payload_id != device_id:
                raise contrato.ErroContrato("device_id_divergente", f"topico={device_id} payload={payload_id}")

            if tipo == "telemetria":
                _telemetria(conn, device_id, grandeza, dados, chegada)
            elif tipo == "estado":
                _estado(conn, device_id, dados)
            elif tipo == "status":
                _status(conn, device_id, dados, pendentes)
            elif tipo == "confirmacao":
                _confirmacao(conn, device_id, dados)
            else:
                raise contrato.ErroContrato("topico_desconhecido", tipo)
        except contrato.ErroContrato as erro:
            tipo_evento = "falha_sensor" if erro.codigo == "falha_sensor" else "mensagem_invalida"
            detalhe = erro.detalhe if erro.codigo == "falha_sensor" else f"{erro.codigo} em {topico}: {erro.detalhe}"
            db.registrar_evento(conn, device_id, tipo_evento, detalhe)
            log.warning("%s %s: %s", tipo_evento, topico, erro)

    for args in pendentes:
        notify.agendar(*args)


def _tocar(conn: Connection, device_id: str) -> None:
    conn.execute(text("UPDATE dispositivos SET ultimo_contato = now() WHERE id = :d"), {"d": device_id})


def _telemetria(conn: Connection, device_id: str, grandeza: str, dados: dict, chegada) -> None:
    _tocar(conn, device_id)
    valor, unidade = contrato.validar_telemetria(grandeza, dados)
    medido_em = contrato.interpretar_ts(dados.get("ts"), chegada)
    conn.execute(
        text(
            """
            INSERT INTO leituras (dispositivo_id, grandeza, valor, unidade, medido_em, recebido_em)
            VALUES (:d, :g, :v, :u, :m, :r)
            """
        ),
        {"d": device_id, "g": grandeza, "v": valor, "u": unidade, "m": medido_em, "r": chegada},
    )


def _estado(conn: Connection, device_id: str, dados: dict) -> None:
    campos = contrato.validar_estado(dados)
    anterior = db.linha(conn, "SELECT fw FROM dispositivos WHERE id = :d FOR UPDATE", d=device_id)
    conn.execute(
        text(
            """
            UPDATE dispositivos
            SET modo = COALESCE(:modo, modo),
                condicao = COALESCE(:condicao, condicao),
                fw = COALESCE(:fw, fw),
                ultimo_estado = CAST(:estado AS jsonb),
                ultimo_contato = now()
            WHERE id = :d
            """
        ),
        {**campos, "estado": json.dumps(dados, ensure_ascii=False), "d": device_id},
    )
    if campos["fw"] and anterior["fw"] and campos["fw"] != anterior["fw"]:
        db.registrar_evento(conn, device_id, "firmware_atualizado", f"{anterior['fw']} -> {campos['fw']}")
    promovidos = comandos.promover_executados(conn, device_id)
    if promovidos:
        log.info("%s: %d comando(s) confirmado(s) pelo estado", device_id, promovidos)


def _status(conn: Connection, device_id: str, dados: dict, pendentes: list) -> None:
    status = contrato.validar_status(dados)
    online = status == "online"
    anterior = db.linha(conn, "SELECT online FROM dispositivos WHERE id = :d FOR UPDATE", d=device_id)
    conn.execute(
        text(
            "UPDATE dispositivos SET online = :o, ultimo_contato = CASE WHEN :o THEN now() ELSE ultimo_contato END WHERE id = :d"
        ),
        {"o": online, "d": device_id},
    )
    if anterior["online"] != online:
        db.registrar_evento(conn, device_id, "status", status)

    if online:
        alertas.resolver(conn, device_id, "dispositivo_offline")
    elif alertas.abrir(conn, device_id, "dispositivo_offline", f"{device_id} ficou offline"):
        pendentes.append(
            (device_id, "dispositivo_offline", f"[MACP] {device_id} offline",
             f"A estacao {device_id} perdeu a conexao com o broker (LWT recebido).")
        )


def _confirmacao(conn: Connection, device_id: str, dados: dict) -> None:
    _tocar(conn, device_id)
    conf = contrato.validar_confirmacao(dados)
    comandos.aplicar_confirmacao(conn, device_id, conf)
