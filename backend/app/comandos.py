import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import contrato, db
from .config import config

log = logging.getLogger("macp.comandos")

ORDEM = {"enviado": 0, "recebido": 1, "executado": 2, "confirmado": 3}
FINAIS = {"confirmado", "rejeitado", "falhou", "expirado"}
ORIGENS_PERMITIDAS = {
    "rejeitado": {"enviado", "recebido"},
    "falhou": {"enviado", "recebido", "executado"},
    "expirado": {"enviado"},
}
COLUNA_HORA = {
    "recebido": "recebido_em",
    "executado": "executado_em",
    "confirmado": "confirmado_em",
}

COLUNAS = """
    comando_id, dispositivo_id, alvo, acao, duracao_s, parametros, origem, usuario_id,
    estado, motivo, enviado_em, recebido_em, executado_em, confirmado_em, atualizado_em
"""


class DispositivoDesconhecido(Exception):
    pass


class DispositivoOffline(Exception):
    pass


class BrokerIndisponivel(Exception):
    def __init__(self, comando: dict):
        super().__init__("broker_indisponivel")
        self.comando = comando


def pode_avancar(atual: str, novo: str) -> bool:
    if atual in FINAIS or novo == atual:
        return False
    if novo in ORIGENS_PERMITIDAS:
        return atual in ORIGENS_PERMITIDAS[novo]
    if novo not in ORDEM or atual not in ORDEM:
        return False
    if ORDEM[novo] <= ORDEM[atual]:
        return False
    return True


def topico_comando(dispositivo_id: str) -> str:
    return f"{config.mqtt_prefixo}/{dispositivo_id}/comando"


def _novo_command_id(conn: Connection, agora: datetime) -> str:
    numero = conn.execute(text("SELECT nextval('comando_seq')")).scalar_one()
    return contrato.formatar_command_id(agora.astimezone(ZoneInfo(config.fuso)), numero)


def criar_comando(
    ponte,
    dispositivo_id: str,
    alvo,
    acao=None,
    duracao=None,
    parametros=None,
    origem: str = "usuario",
    usuario_id: int | None = None,
) -> dict:
    comando = contrato.validar_comando(alvo, acao, duracao, parametros)
    agora = contrato.agora_utc()

    with db.transacao() as conn:
        dispositivo = db.linha(conn, "SELECT id, online, recursos FROM dispositivos WHERE id = :d", d=dispositivo_id)
        if dispositivo is None:
            raise DispositivoDesconhecido(dispositivo_id)
        if comando["alvo"] in contrato.ATUADORES and \
                comando["alvo"] not in contrato.recursos_efetivos(dispositivo["recursos"])["atuadores"]:
            # nem chega a publicar: o firmware rejeitaria com o mesmo motivo
            raise contrato.ErroContrato("alvo_invalido", f"{dispositivo_id} nao tem {comando['alvo']}")
        broker_ok = ponte is not None and ponte.conectado
        if broker_ok and not dispositivo["online"]:
            raise DispositivoOffline(dispositivo_id)
        comando_id = _novo_command_id(conn, agora)
        registro = db.linha(
            conn,
            f"""
            INSERT INTO comandos (comando_id, dispositivo_id, alvo, acao, duracao_s, parametros,
                                  origem, usuario_id, estado, enviado_em, atualizado_em)
            VALUES (:id, :d, :alvo, :acao, :dur, CAST(:par AS jsonb), :origem, :u, 'enviado', :t, :t)
            RETURNING {COLUNAS}
            """,
            id=comando_id,
            d=dispositivo_id,
            alvo=comando["alvo"],
            acao=comando["acao"],
            dur=comando["duracaoS"],
            par=json.dumps(comando["parametros"]) if comando["parametros"] else None,
            origem=origem,
            u=usuario_id,
            t=agora,
        )

    payload = contrato.montar_payload_comando(comando_id, agora.astimezone(ZoneInfo(config.fuso)), comando, origem)
    publicado = False
    try:
        publicado = ponte is not None and ponte.publicar(topico_comando(dispositivo_id), payload, qos=1)
    except Exception:
        log.exception("erro ao publicar comando %s", comando_id)
    if not publicado:
        with db.transacao() as conn:
            registro = db.linha(
                conn,
                f"""
                UPDATE comandos SET estado = 'falhou', motivo = 'broker_indisponivel', atualizado_em = now()
                WHERE comando_id = :id RETURNING {COLUNAS}
                """,
                id=comando_id,
            )
            db.registrar_evento(conn, dispositivo_id, "comando_falhou", f"{comando_id} broker_indisponivel")
        raise BrokerIndisponivel(registro)

    log.info("comando %s publicado: %s", comando_id, payload)
    return registro


def aplicar_confirmacao(conn: Connection, dispositivo_id: str, conf: dict) -> bool:
    atual = db.linha(
        conn,
        "SELECT estado FROM comandos WHERE comando_id = :id AND dispositivo_id = :d FOR UPDATE",
        id=conf["commandId"],
        d=dispositivo_id,
    )
    if atual is None:
        db.registrar_evento(conn, dispositivo_id, "mensagem_invalida", f"confirmacao de comando desconhecido {conf['commandId']}")
        return False

    novo = contrato.estado_da_confirmacao(conf["etapa"], conf["resultado"])
    if not pode_avancar(atual["estado"], novo):
        log.info("confirmacao ignorada %s: %s -> %s", conf["commandId"], atual["estado"], novo)
        return False

    if novo in ("rejeitado", "falhou") and not conf["motivo_no_catalogo"]:
        db.registrar_evento(conn, dispositivo_id, "mensagem_invalida", f"motivo fora do catalogo: {conf['motivo']}")

    coluna = COLUNA_HORA.get(novo) or COLUNA_HORA.get(conf["etapa"])
    hora = f", {coluna} = COALESCE({coluna}, now())" if coluna else ""
    conn.execute(
        text(
            f"""
            UPDATE comandos
            SET estado = :novo, motivo = COALESCE(:motivo, motivo), atualizado_em = now() {hora}
            WHERE comando_id = :id
            """
        ),
        {"novo": novo, "motivo": conf["motivo"] if novo in ("rejeitado", "falhou") else None, "id": conf["commandId"]},
    )
    return True


def promover_executados(conn: Connection, dispositivo_id: str) -> int:
    resultado = conn.execute(
        text(
            """
            UPDATE comandos
            SET estado = 'confirmado', confirmado_em = now(), atualizado_em = now()
            WHERE dispositivo_id = :d AND estado = 'executado' AND executado_em <= now()
            """
        ),
        {"d": dispositivo_id},
    )
    return resultado.rowcount


def expirar_pendentes(timeout_s: int | None = None) -> int:
    timeout_s = timeout_s if timeout_s is not None else config.comando_timeout_s
    with db.transacao() as conn:
        expirados = db.linhas(
            conn,
            """
            UPDATE comandos SET estado = 'expirado', atualizado_em = now()
            WHERE estado = 'enviado' AND enviado_em < now() - make_interval(secs => :s)
            RETURNING comando_id, dispositivo_id
            """,
            s=timeout_s,
        )
        for c in expirados:
            db.registrar_evento(conn, c["dispositivo_id"], "comando_expirado", c["comando_id"])
    if expirados:
        log.info("comandos expirados: %s", [c["comando_id"] for c in expirados])
    return len(expirados)
