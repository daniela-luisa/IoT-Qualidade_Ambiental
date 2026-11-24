from datetime import datetime, timezone

import pytest

from app import contrato
from app.contrato import ErroContrato

CHEGADA = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


def test_json_valido_v1():
    assert contrato.ler_json(b'{"v":1,"valor":2}')["valor"] == 2


@pytest.mark.parametrize("payload", [b"nao e json", b"[1,2]", b"\xff\xfe", b""])
def test_json_invalido(payload):
    with pytest.raises(ErroContrato) as e:
        contrato.ler_json(payload)
    assert e.value.codigo == "json_invalido"


@pytest.mark.parametrize("payload", [b'{"v":2}', b'{"valor":1}', b'{"v":"1"}', b'{"v":true}'])
def test_versao_nao_suportada(payload):
    with pytest.raises(ErroContrato) as e:
        contrato.ler_json(payload)
    assert e.value.codigo == "versao_nao_suportada"


@pytest.mark.parametrize(
    "grandeza,valor",
    [("temperatura", 0), ("temperatura", 50), ("umidade_ar", 20), ("umidade_ar", 90),
     ("umidade_solo", 0), ("umidade_solo", 100), ("luminosidade", 55.5)],
)
def test_telemetria_dentro_da_faixa(grandeza, valor):
    v, unidade = contrato.validar_telemetria(grandeza, {"valor": valor})
    assert v == valor
    assert unidade == contrato.GRANDEZAS[grandeza][2]


@pytest.mark.parametrize(
    "grandeza,valor",
    [("temperatura", -0.1), ("temperatura", 50.1), ("umidade_ar", 19.9), ("umidade_ar", 91),
     ("umidade_solo", 101), ("luminosidade", -1), ("temperatura", None), ("temperatura", "24"),
     ("temperatura", True)],
)
def test_telemetria_fora_da_faixa_ou_nula_e_falha_de_sensor(grandeza, valor):
    with pytest.raises(ErroContrato) as e:
        contrato.validar_telemetria(grandeza, {"valor": valor})
    assert e.value.codigo == "falha_sensor"


def test_grandeza_fora_do_contrato():
    with pytest.raises(ErroContrato) as e:
        contrato.validar_telemetria("solo", {"valor": 10})
    assert e.value.codigo == "grandeza_invalida"


def test_ts_nulo_usa_hora_de_chegada():
    assert contrato.interpretar_ts(None, CHEGADA) == CHEGADA


def test_ts_com_fuso_e_respeitado():
    ts = contrato.interpretar_ts("2026-11-24T09:15:00-03:00", CHEGADA)
    assert ts.utcoffset().total_seconds() == -3 * 3600
    assert ts.hour == 9


@pytest.mark.parametrize("ts", ["2026-11-24T09:15:00", "1970-01-01T00:00:10+00:00", "ontem", 12345])
def test_ts_sem_fuso_invalido_ou_sem_ntp_usa_chegada(ts):
    assert contrato.interpretar_ts(ts, CHEGADA) == CHEGADA


def test_estado_ignora_modo_fora_do_vocabulario():
    campos = contrato.validar_estado({"modo": "MANUAL", "condicao": "atencao", "fw": "1.0.0"})
    assert campos == {"modo": None, "condicao": "atencao", "fw": "1.0.0", "recursos": None}


def test_status_online_offline():
    assert contrato.validar_status({"status": "offline"}) == "offline"
    with pytest.raises(ErroContrato):
        contrato.validar_status({"status": "caiu"})


def test_confirmacao_valida_e_motivo_do_catalogo():
    c = contrato.validar_confirmacao(
        {"commandId": "c-20261124-0008", "etapa": "recebido", "resultado": "rejeitado",
         "motivo": "intertravamento_intervalo"}
    )
    assert c["motivo_no_catalogo"] is True
    assert contrato.estado_da_confirmacao(c["etapa"], c["resultado"]) == "rejeitado"


def test_confirmacao_com_motivo_fora_do_catalogo_e_marcada():
    c = contrato.validar_confirmacao(
        {"commandId": "c-20261124-0008", "etapa": "recebido", "resultado": "rejeitado", "motivo": "qualquer"}
    )
    assert c["motivo_no_catalogo"] is False


@pytest.mark.parametrize(
    "dados",
    [{"commandId": "123", "etapa": "recebido"},
     {"commandId": "c-20261124-0001", "etapa": "terminado"},
     {"commandId": "c-20261124-0001", "etapa": "executado", "resultado": "talvez"}],
)
def test_confirmacao_invalida(dados):
    with pytest.raises(ErroContrato):
        contrato.validar_confirmacao(dados)


def test_catalogo_de_motivos_tem_os_9_do_contrato():
    assert contrato.MOTIVOS == {
        "intertravamento_intervalo", "intertravamento_duracao", "modo_seguro", "comando_duplicado",
        "alvo_invalido", "valor_fora_da_faixa", "json_invalido", "versao_nao_suportada",
        "bomba_bloqueada",
    }


@pytest.mark.parametrize("alvo,padrao", [("bomba", 3), ("led", 8), ("buzzer", 8)])
def test_duracao_padrao(alvo, padrao):
    assert contrato.validar_comando(alvo, "on", None, None)["duracaoS"] == padrao


@pytest.mark.parametrize("alvo,duracao", [("bomba", 1), ("bomba", 15), ("led", 60), ("buzzer", 1)])
def test_duracao_nos_limites_e_aceita(alvo, duracao):
    assert contrato.validar_comando(alvo, "on", duracao, None)["duracaoS"] == duracao


@pytest.mark.parametrize(
    "alvo,duracao", [("bomba", 0), ("bomba", 16), ("bomba", 99), ("led", 61), ("buzzer", 0), ("led", 2.5)]
)
def test_duracao_fora_da_faixa(alvo, duracao):
    with pytest.raises(ErroContrato) as e:
        contrato.validar_comando(alvo, "on", duracao, None)
    assert e.value.codigo == "valor_fora_da_faixa"


def test_comando_off_sem_duracao():
    c = contrato.validar_comando("bomba", "off", 10, None)
    assert c == {"alvo": "bomba", "acao": "off", "duracaoS": None, "parametros": None}


def test_alvo_invalido():
    with pytest.raises(ErroContrato) as e:
        contrato.validar_comando("ventilador", "on", 5, None)
    assert e.value.codigo == "alvo_invalido"


def test_config_valido_vai_no_nivel_de_cima_do_payload():
    c = contrato.validar_comando("config", "set", None, {"limiarSoloSecoPct": 35})
    payload = contrato.montar_payload_comando("c-20261124-0001", CHEGADA, c, "usuario")
    assert payload["limiarSoloSecoPct"] == 35
    assert payload["alvo"] == "config" and payload["acao"] == "set"
    assert "duracaoS" not in payload and payload["v"] == 1


@pytest.mark.parametrize(
    "parametros",
    [{"limiarSoloSecoPct": 9}, {"intervaloMinS": 3601}, {"duracaoMaxS": 16}, {"outro": 1}, {}, None,
     {"limiarSoloSecoPct": 30.5}, {"intervaloMinS": "60"}, {"duracaoMaxS": True}],
)
def test_config_invalido(parametros):
    with pytest.raises(ErroContrato) as e:
        contrato.validar_comando("config", "set", None, parametros)
    assert e.value.codigo == "valor_fora_da_faixa"


def test_formato_do_command_id():
    assert contrato.formatar_command_id(CHEGADA, 7) == "c-20261124-0007"


def test_config_inteiro_vindo_como_float_vira_int():
    c = contrato.validar_comando("config", "set", None, {"limiarSoloSecoPct": 35.0})
    assert c["parametros"] == {"limiarSoloSecoPct": 35}
    assert isinstance(c["parametros"]["limiarSoloSecoPct"], int)
