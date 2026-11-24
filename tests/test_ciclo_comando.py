from datetime import timedelta

import pytest

from app import comandos, contrato
from conftest import consulta, publicar

pytestmark = pytest.mark.usefixtures("banco")

D = "estacao-01"


def ficar_online(device=D):
    publicar(device, "status", {"v": 1, "deviceId": device, "status": "online"})


def test_telemetria_valida_e_gravada():
    publicar(D, "telemetria/temperatura",
             {"v": 1, "deviceId": D, "ts": "2026-11-24T09:15:00-03:00", "grandeza": "temperatura",
              "valor": 24.6, "unidade": "°C"})
    [l] = consulta("SELECT grandeza, valor, unidade, medido_em FROM leituras")
    assert (l["grandeza"], l["valor"], l["unidade"]) == ("temperatura", 24.6, "°C")
    assert l["medido_em"].isoformat().startswith("2026-11-24T12:15:00")
    [d] = consulta("SELECT ultimo_contato FROM dispositivos WHERE id = :d", d=D)
    assert d["ultimo_contato"] is not None


def test_ts_nulo_grava_hora_de_chegada():
    publicar(D, "telemetria/umidade_ar", {"v": 1, "deviceId": D, "ts": None, "valor": 55})
    [l] = consulta("SELECT medido_em, recebido_em FROM leituras")
    assert l["medido_em"] == l["recebido_em"]


@pytest.mark.parametrize("valor", [None, 51, -5])
def test_falha_de_sensor_nao_grava_e_vira_evento(valor):
    publicar(D, "telemetria/temperatura", {"v": 1, "deviceId": D, "ts": None, "valor": valor})
    assert consulta("SELECT * FROM leituras") == []
    [e] = consulta("SELECT dispositivo_id, tipo FROM eventos")
    assert e == {"dispositivo_id": D, "tipo": "falha_sensor"}


def test_mensagem_ruim_nao_derruba_e_a_proxima_e_processada():
    publicar(D, "telemetria/temperatura", b"{isso nao e json")
    publicar(D, "telemetria/temperatura", {"v": 9, "valor": 20})
    publicar(D, "telemetria/temperatura", {"v": 1, "valor": 21.5})
    tipos = [e["tipo"] for e in consulta("SELECT tipo FROM eventos ORDER BY id")]
    assert tipos == ["mensagem_invalida", "mensagem_invalida"]
    assert [l["valor"] for l in consulta("SELECT valor FROM leituras")] == [21.5]


def test_device_id_fora_do_padrao_e_ignorado_e_desconhecido_vira_evento():
    publicar("sala", "telemetria/temperatura", {"v": 1, "valor": 20})
    assert consulta("SELECT * FROM eventos") == []
    publicar("estacao-99", "telemetria/temperatura", {"v": 1, "valor": 20})
    [e] = consulta("SELECT dispositivo_id, tipo FROM eventos")
    assert e == {"dispositivo_id": None, "tipo": "mensagem_invalida"}
    assert consulta("SELECT * FROM leituras") == []


def test_lwt_offline_abre_um_alerta_so():
    ficar_online()
    offline = {"v": 1, "deviceId": D, "status": "offline"}
    publicar(D, "status", offline)
    publicar(D, "status", offline)
    assert consulta("SELECT online FROM dispositivos WHERE id = :d", d=D) == [{"online": False}]
    assert len(consulta("SELECT * FROM alertas WHERE resolvido_em IS NULL")) == 1
    eventos = [e["detalhe"] for e in consulta("SELECT detalhe FROM eventos WHERE tipo = 'status' ORDER BY id")]
    assert eventos == ["online", "offline"]


def test_evento_guardado_offline_vai_para_o_historico_com_o_ts_original():
    publicar(D, "evento", {"v": 1, "deviceId": D, "ts": "2026-11-24T09:15:00-03:00",
                            "tipo": "bomba", "detalhe": "on (modo automatico, offline)"})
    [e] = consulta("SELECT tipo, detalhe, criado_em FROM eventos")
    assert (e["tipo"], e["detalhe"]) == ("bomba", "on (modo automatico, offline)")
    assert e["criado_em"].isoformat().startswith("2026-11-24T12:15:00")


@pytest.mark.parametrize("dados", [{"tipo": "xyz", "detalhe": "a"}, {"tipo": "modo", "detalhe": ""}])
def test_evento_invalido_vira_mensagem_invalida(dados):
    publicar(D, "evento", {"v": 1, "deviceId": D, "ts": None, **dados})
    [e] = consulta("SELECT tipo FROM eventos")
    assert e["tipo"] == "mensagem_invalida"


def test_voltar_online_resolve_alerta():
    ficar_online()
    publicar(D, "status", {"v": 1, "deviceId": D, "status": "offline"})
    ficar_online()
    assert consulta("SELECT * FROM alertas WHERE resolvido_em IS NULL") == []


def test_estado_atualiza_dispositivo_e_ignora_modo_invalido():
    publicar(D, "estado", {"v": 1, "deviceId": D, "modo": "automatico", "condicao": "atencao",
                           "bomba": "off", "led": "off", "buzzer": "off", "fw": "1.0.0"})
    publicar(D, "estado", {"v": 1, "deviceId": D, "modo": "MANUAL", "condicao": "adequado", "fw": "1.0.0"})
    [d] = consulta("SELECT modo, condicao, fw, ultimo_estado FROM dispositivos WHERE id = :d", d=D)
    assert (d["modo"], d["condicao"], d["fw"]) == ("automatico", "adequado", "1.0.0")
    assert d["ultimo_estado"]["modo"] == "MANUAL"


def test_troca_de_firmware_vira_evento():
    publicar(D, "estado", {"v": 1, "deviceId": D, "fw": "1.0.0"})
    publicar(D, "estado", {"v": 1, "deviceId": D, "fw": "1.1.0"})
    [e] = consulta("SELECT detalhe FROM eventos WHERE tipo = 'firmware_atualizado'")
    assert e["detalhe"] == "1.0.0 -> 1.1.0"


def criar(ponte, alvo="bomba", duracao=5):
    ficar_online()
    return comandos.criar_comando(ponte, D, alvo, "on", duracao)


def conf(cid, etapa, resultado="ok", motivo=None):
    publicar(D, "confirmacao", {"v": 1, "deviceId": D, "commandId": cid, "ts": None,
                                "etapa": etapa, "resultado": resultado, "motivo": motivo})


def estado(cid):
    return consulta("SELECT estado FROM comandos WHERE comando_id = :c", c=cid)[0]["estado"]


def test_ciclo_completo_ate_confirmado(ponte):
    c = criar(ponte)
    assert c["estado"] == "enviado"
    assert c["comando_id"].startswith("c-") and len(c["comando_id"]) == len("c-20261124-0001")
    conf(c["comando_id"], "recebido")
    assert estado(c["comando_id"]) == "recebido"
    conf(c["comando_id"], "executado")
    assert estado(c["comando_id"]) == "executado"
    publicar(D, "estado", {"v": 1, "deviceId": D, "modo": "automatico", "bomba": "on"})
    assert estado(c["comando_id"]) == "confirmado"
    [linha] = consulta("SELECT recebido_em, executado_em, confirmado_em FROM comandos")
    assert all(linha.values())


def test_etapa_confirmado_do_dispositivo_tambem_confirma(ponte):
    c = criar(ponte, "led", 3)
    conf(c["comando_id"], "recebido")
    conf(c["comando_id"], "executado")
    conf(c["comando_id"], "confirmado")
    assert estado(c["comando_id"]) == "confirmado"


def test_estado_antes_do_executado_nao_confirma(ponte):
    c = criar(ponte)
    conf(c["comando_id"], "recebido")
    publicar(D, "estado", {"v": 1, "deviceId": D, "bomba": "off"})
    assert estado(c["comando_id"]) == "recebido"


def test_rejeicao_com_motivo(ponte):
    c = criar(ponte)
    conf(c["comando_id"], "recebido", "rejeitado", "intertravamento_intervalo")
    [linha] = consulta("SELECT estado, motivo FROM comandos")
    assert linha == {"estado": "rejeitado", "motivo": "intertravamento_intervalo"}


def test_rejeitado_nao_volta_a_executado(ponte):
    c = criar(ponte)
    conf(c["comando_id"], "recebido", "rejeitado", "modo_seguro")
    conf(c["comando_id"], "executado")
    publicar(D, "estado", {"v": 1, "deviceId": D})
    assert estado(c["comando_id"]) == "rejeitado"


def test_confirmacao_repetida_ou_fora_de_ordem_e_ignorada(ponte):
    c = criar(ponte)
    conf(c["comando_id"], "executado")
    conf(c["comando_id"], "recebido")
    conf(c["comando_id"], "executado")
    assert estado(c["comando_id"]) == "executado"


def test_motivo_fora_do_catalogo_e_gravado_com_rastro(ponte):
    c = criar(ponte)
    conf(c["comando_id"], "recebido", "rejeitado", "bateria_fraca")
    assert estado(c["comando_id"]) == "rejeitado"
    [e] = consulta("SELECT detalhe FROM eventos WHERE tipo = 'mensagem_invalida'")
    assert "bateria_fraca" in e["detalhe"]


def test_confirmacao_de_comando_desconhecido_vira_evento():
    conf("c-20261124-9999", "recebido")
    [e] = consulta("SELECT tipo FROM eventos")
    assert e["tipo"] == "mensagem_invalida"


def test_expira_sem_resposta(ponte):
    c = criar(ponte)
    assert comandos.expirar_pendentes(timeout_s=30) == 0
    consulta("UPDATE comandos SET enviado_em = now() - interval '31 seconds' RETURNING comando_id")
    assert comandos.expirar_pendentes(timeout_s=30) == 1
    assert estado(c["comando_id"]) == "expirado"
    conf(c["comando_id"], "recebido")
    assert estado(c["comando_id"]) == "expirado"


def test_comando_recebido_nao_expira(ponte):
    c = criar(ponte)
    conf(c["comando_id"], "recebido")
    consulta("UPDATE comandos SET enviado_em = now() - interval '5 minutes' RETURNING comando_id")
    assert comandos.expirar_pendentes(timeout_s=30) == 0


@pytest.mark.parametrize(
    "atual,novo,esperado",
    [("enviado", "recebido", True), ("recebido", "executado", True), ("executado", "confirmado", True),
     ("enviado", "executado", True), ("executado", "recebido", False), ("recebido", "recebido", False),
     ("confirmado", "falhou", False), ("rejeitado", "executado", False), ("executado", "rejeitado", False),
     ("recebido", "expirado", False), ("executado", "falhou", True)],
)
def test_pode_avancar(atual, novo, esperado):
    assert comandos.pode_avancar(atual, novo) is esperado


def test_command_id_unico_e_sequencial(ponte):
    a, b = criar(ponte), criar(ponte, "led", 2)
    na, nb = int(a["comando_id"].rsplit("-", 1)[1]), int(b["comando_id"].rsplit("-", 1)[1])
    assert nb == na + 1


def test_payload_publicado_segue_o_contrato(ponte):
    c = criar(ponte, "bomba", 7)
    [(topico, payload, qos)] = ponte.publicados
    assert topico == f"grupo5/{D}/comando" and qos == 1
    assert payload["v"] == 1 and payload["commandId"] == c["comando_id"]
    assert (payload["alvo"], payload["acao"], payload["duracaoS"], payload["origem"]) == ("bomba", "on", 7, "usuario")
    ts = contrato.interpretar_ts(payload["ts"], None)
    assert ts is not None and ts.utcoffset() == timedelta(hours=-3)


def test_falha_de_sensor_repetida_vira_um_evento_por_minuto():
    for _ in range(5):
        publicar(D, "telemetria/umidade_solo", {"v": 1, "deviceId": D, "ts": None, "valor": None})
    publicar(D, "telemetria/temperatura", {"v": 1, "deviceId": D, "ts": None, "valor": None})
    detalhes = [e["detalhe"] for e in consulta("SELECT detalhe FROM eventos WHERE tipo = 'falha_sensor' ORDER BY id")]
    assert len(detalhes) == 2
    assert detalhes[0].startswith("umidade_solo ") and detalhes[1].startswith("temperatura ")


def test_borda_da_bomba_no_estado_vira_evento():
    estado = {"v": 1, "deviceId": D, "modo": "automatico", "condicao": "inadequado", "led": "off", "buzzer": "off"}
    publicar(D, "estado", {**estado, "bomba": "off"})
    publicar(D, "estado", {**estado, "bomba": "on"})
    publicar(D, "estado", {**estado, "bomba": "on"})
    publicar(D, "estado", {**estado, "bomba": "off"})
    detalhes = [e["detalhe"] for e in consulta("SELECT detalhe FROM eventos WHERE tipo = 'bomba' ORDER BY id")]
    assert detalhes == ["on (modo automatico)", "off (modo automatico)"]
