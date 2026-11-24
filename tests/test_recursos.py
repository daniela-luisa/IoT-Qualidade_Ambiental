"""Estacao sem bomba: recursos declarados no estado, comando recusado e aviso por LED + buzzer."""

import pytest

from app import contrato, db, regras
from conftest import consulta, publicar

pytestmark = pytest.mark.usefixtures("banco")

D = "estacao-02"
SENSORES = ["temperatura", "umidade_ar", "umidade_solo", "luminosidade"]
SEM_BOMBA = {"sensores": SENSORES, "atuadores": ["led", "buzzer"]}


def estado(recursos=SEM_BOMBA, **extra):
    dados = {"v": 1, "deviceId": D, "modo": "automatico", "condicao": "adequado", "led": "off", "buzzer": "off", **extra}
    if recursos is not None:
        dados["recursos"] = recursos
    publicar(D, "estado", dados)


def preparar_sem_bomba():
    publicar(D, "status", {"v": 1, "deviceId": D, "status": "online"})
    estado()


def solo(valor):
    publicar(D, "telemetria/umidade_solo", {"v": 1, "deviceId": D, "ts": None, "valor": valor})


def comandos_regra():
    return consulta("SELECT alvo, acao, duracao_s FROM comandos WHERE origem = 'regra' ORDER BY comando_id")


# ── contrato ──────────────────────────────────────────────────────────────


def test_validar_recursos_descarta_nomes_desconhecidos():
    r = contrato.validar_recursos({"sensores": ["umidade_solo", "pressao"], "atuadores": ["ventilador", "led"]})
    assert r == {"sensores": ["umidade_solo"], "atuadores": ["led"]}


@pytest.mark.parametrize("valor", [None, "led", [], {"sensores": "x", "atuadores": []}, {"atuadores": ["led"]}])
def test_recursos_invalidos_viram_none_e_efetivo_e_tudo(valor):
    assert contrato.validar_recursos(valor) is None
    assert contrato.recursos_efetivos(valor) == {"sensores": SENSORES, "atuadores": ["bomba", "led", "buzzer"]}


# ── estado e API ──────────────────────────────────────────────────────────


def test_estado_grava_recursos_e_gera_evento_uma_vez():
    estado()
    estado()  # mesmo conteudo: sem evento novo
    [d] = consulta("SELECT recursos FROM dispositivos WHERE id = :d", d=D)
    assert d["recursos"] == SEM_BOMBA
    assert [e["detalhe"] for e in consulta("SELECT detalhe FROM eventos WHERE tipo = 'recursos'")] == ["atuadores: led, buzzer"]


def test_estado_sem_recursos_mantem_o_que_ja_sabia():
    estado()
    estado(recursos=None)  # firmware antigo / campo ausente
    [d] = consulta("SELECT recursos FROM dispositivos WHERE id = :d", d=D)
    assert d["recursos"] == SEM_BOMBA


def test_api_dispositivos_mostra_recursos(cliente, h_operador):
    estado()
    e1, e2 = cliente.get("/api/dispositivos", headers=h_operador).json()
    assert e1["recursos"]["atuadores"] == ["bomba", "led", "buzzer"] and e1["recursos_declarados"] is False
    assert e2["recursos"] == SEM_BOMBA and e2["recursos_declarados"] is True
    p = cliente.get(f"/api/painel?dispositivo_id={D}", headers=h_operador).json()
    assert p["recursos"] == SEM_BOMBA


def test_comando_de_bomba_para_estacao_sem_bomba_e_recusado_sem_publicar(cliente, ponte, h_admin):
    preparar_sem_bomba()
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "bomba", "duracaoS": 3})
    assert r.status_code == 422 and r.json()["codigo"] == "alvo_invalido"
    assert ponte.publicados == [] and consulta("SELECT * FROM comandos") == []
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "buzzer", "duracaoS": 2})
    assert r.status_code == 201, r.text


# ── regra ─────────────────────────────────────────────────────────────────


def test_sem_bomba_solo_seco_aciona_led_e_buzzer(ponte):
    preparar_sem_bomba()
    solo(10.0)
    regras.avaliar_todas(ponte)
    # LED aceso ate o proximo aviso (intervalo 60 s, teto 60 s); buzzer bipa duracao_s (5 s)
    assert comandos_regra() == [{"alvo": "led", "acao": "on", "duracao_s": 60},
                                {"alvo": "buzzer", "acao": "on", "duracao_s": 5}]
    assert [t for t, _, _ in ponte.publicados] == [f"grupo5/{D}/comando"] * 2
    [a] = consulta("SELECT tipo, mensagem FROM alertas WHERE resolvido_em IS NULL")
    assert a["tipo"] == "solo_seco" and "regar manualmente" in a["mensagem"]
    [n] = consulta("SELECT motivo FROM notificacoes")
    assert n["motivo"] == "solo_seco"


def test_sem_bomba_repete_o_aviso_so_depois_do_intervalo(ponte):
    preparar_sem_bomba()
    solo(10.0)
    regras.avaliar_todas(ponte)
    regras.avaliar_todas(ponte)
    assert len(comandos_regra()) == 2
    with db.transacao() as conn:
        conn.exec_driver_sql("UPDATE comandos SET enviado_em = now() - interval '90 seconds'")
    regras.avaliar_todas(ponte)
    assert [c["alvo"] for c in comandos_regra()] == ["led", "buzzer", "led", "buzzer"]


def test_sem_bomba_solo_recuperado_apaga_led_e_buzzer(ponte):
    preparar_sem_bomba()
    solo(10.0)
    regras.avaliar_todas(ponte)
    solo(70.0)  # alguem regou: media 40 >= 30
    regras.avaliar_todas(ponte)
    assert [(c["alvo"], c["acao"]) for c in comandos_regra()][-2:] == [("led", "off"), ("buzzer", "off")]
    assert consulta("SELECT * FROM alertas WHERE resolvido_em IS NULL") == []


def test_com_bomba_continua_irrigando(ponte):
    publicar("estacao-01", "status", {"v": 1, "deviceId": "estacao-01", "status": "online"})
    publicar("estacao-01", "estado", {"v": 1, "modo": "automatico", "bomba": "off",
                                      "recursos": {"sensores": SENSORES, "atuadores": ["bomba", "led", "buzzer"]}})
    publicar("estacao-01", "telemetria/umidade_solo", {"v": 1, "ts": None, "valor": 10.0})
    regras.avaliar_todas(ponte)
    assert [(c["alvo"], c["acao"]) for c in comandos_regra()] == [("bomba", "on")]


def test_migracao_adiciona_coluna_em_banco_antigo():
    with db.transacao() as conn:
        conn.exec_driver_sql("ALTER TABLE dispositivos DROP COLUMN recursos")
    db.migrar()
    db.migrar()  # idempotente
    assert consulta("SELECT recursos FROM dispositivos WHERE id = :d", d=D) == [{"recursos": None}]
