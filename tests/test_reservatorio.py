from datetime import timedelta

import pytest

from app import contrato, db, regras
from conftest import consulta, publicar

pytestmark = pytest.mark.usefixtures("banco")

D = "estacao-01"


def ficar_online(bloqueada=0):
    publicar(D, "status", {"v": 1, "deviceId": D, "status": "online"})
    publicar(D, "estado", {"v": 1, "deviceId": D, "modo": "automatico", "condicao": "inadequado",
                           "bomba": "off", "led": "off", "buzzer": "off", "bombaBloqueada": bloqueada})


def ts(minutos_atras: float) -> str:
    return (contrato.agora_utc() - timedelta(minutes=minutos_atras)).isoformat(timespec="seconds")


def solo(valor, minutos_atras):
    publicar(D, "telemetria/umidade_solo", {"v": 1, "deviceId": D, "ts": ts(minutos_atras), "valor": valor})


def bomba_ligou(minutos_atras):
    publicar(D, "evento", {"v": 1, "deviceId": D, "ts": ts(minutos_atras), "tipo": "bomba",
                            "detalhe": "on (modo automatico, offline)"})


def irrigou_sem_efeito(depois=10.5):
    solo(10.0, 9)
    for m in (8, 6, 4):
        bomba_ligou(m)
    solo(depois, 2)


def comandos(origem=None):
    return consulta(
        "SELECT alvo, acao, duracao_s, parametros, origem FROM comandos "
        "WHERE (CAST(:o AS text) IS NULL OR origem = :o) ORDER BY comando_id", o=origem)


def alerta_aberto():
    return consulta("SELECT id, tipo, mensagem FROM alertas WHERE tipo = 'reservatorio_vazio' AND resolvido_em IS NULL")


def test_tres_irrigacoes_sem_o_solo_subir_abre_alerta_toca_alarme_e_bloqueia(ponte):
    ficar_online()
    irrigou_sem_efeito()
    regras.avaliar_reservatorio(ponte)
    [a] = alerta_aberto()
    assert "reservatorio vazio" in a["mensagem"]
    assert comandos() == [
        {"alvo": "config", "acao": "set", "duracao_s": None, "parametros": {"bombaBloqueada": 1}, "origem": "regra"},
        {"alvo": "buzzer", "acao": "on", "duracao_s": regras.ALARME_BUZZER_S, "parametros": None, "origem": "regra"},
        {"alvo": "led", "acao": "on", "duracao_s": regras.ALARME_LED_S, "parametros": None, "origem": "regra"},
    ]


def test_solo_subiu_nao_e_reservatorio_vazio(ponte):
    ficar_online()
    irrigou_sem_efeito(depois=40.0)
    regras.avaliar_reservatorio(ponte)
    assert alerta_aberto() == [] and comandos() == []


def test_menos_de_tres_irrigacoes_ou_agua_ainda_infiltrando_nao_decide(ponte):
    ficar_online()
    solo(10.0, 9)
    bomba_ligou(8)
    bomba_ligou(6)
    solo(10.0, 5)
    regras.avaliar_reservatorio(ponte)
    assert alerta_aberto() == []
    bomba_ligou(0.2)
    solo(10.0, 0.1)
    regras.avaliar_reservatorio(ponte)
    assert alerta_aberto() == []


def test_alarme_nao_repete_antes_do_intervalo_e_para_quando_silenciado(ponte):
    ficar_online(bloqueada=1)
    irrigou_sem_efeito()
    regras.avaliar_reservatorio(ponte)
    regras.avaliar_reservatorio(ponte)
    assert [c["alvo"] for c in comandos()] == ["buzzer", "led"]
    with db.transacao() as conn:
        conn.exec_driver_sql("UPDATE comandos SET enviado_em = now() - interval '11 minutes'")
    publicar(D, "evento", {"v": 1, "deviceId": D, "ts": None, "tipo": "alarme",
                            "detalhe": "silenciado no botao da placa"})
    regras.avaliar_reservatorio(ponte)
    assert len(comandos()) == 2


def test_regra_de_irrigacao_nao_manda_bomba_com_reservatorio_vazio(ponte):
    ficar_online(bloqueada=1)
    irrigou_sem_efeito()
    regras.avaliar_reservatorio(ponte)
    for _ in range(3):
        publicar(D, "telemetria/umidade_solo", {"v": 1, "deviceId": D, "ts": None, "valor": 5.0})
    regras.avaliar_todas(ponte)
    assert "bomba" not in [c["alvo"] for c in comandos()]


def test_reabasteci_resolve_libera_a_bomba_e_nao_reabre_com_as_irrigacoes_antigas(cliente, h_admin, ponte):
    ficar_online(bloqueada=1)
    irrigou_sem_efeito()
    regras.avaliar_reservatorio(ponte)
    [a] = alerta_aberto()

    r = cliente.post(f"/api/alertas/{a['id']}/resolver", headers=h_admin)
    assert r.status_code == 200, r.text
    assert r.json()["desbloqueioEnviado"] is True
    assert alerta_aberto() == []
    assert comandos("usuario")[-1]["parametros"] == {"bombaBloqueada": 0}
    assert consulta("SELECT tipo FROM eventos WHERE tipo = 'reservatorio'") == [{"tipo": "reservatorio"}]

    regras.avaliar_reservatorio(ponte)
    assert alerta_aberto() == []


def test_reabasteci_exige_admin_e_so_vale_para_reservatorio(cliente, h_admin, h_operador, ponte):
    ficar_online()
    irrigou_sem_efeito()
    regras.avaliar_reservatorio(ponte)
    [a] = alerta_aberto()
    assert cliente.post(f"/api/alertas/{a['id']}/resolver", headers=h_operador).status_code == 403
    publicar(D, "status", {"v": 1, "deviceId": D, "status": "offline"})
    [off] = consulta("SELECT id FROM alertas WHERE tipo = 'dispositivo_offline'")
    r = cliente.post(f"/api/alertas/{off['id']}/resolver", headers=h_admin)
    assert r.status_code == 422 and r.json()["codigo"] == "alerta_automatico"


def test_placa_continua_bloqueada_sem_alerta_e_o_backend_desbloqueia(ponte):
    ficar_online(bloqueada=1)
    regras.avaliar_reservatorio(ponte)
    assert comandos()[-1]["parametros"] == {"bombaBloqueada": 0}
    regras.avaliar_reservatorio(ponte)
    assert len(comandos()) == 1


def test_firmware_antigo_sem_o_campo_nao_recebe_config(ponte):
    publicar(D, "status", {"v": 1, "deviceId": D, "status": "online"})
    publicar(D, "estado", {"v": 1, "deviceId": D, "modo": "automatico", "condicao": "adequado", "bomba": "off"})
    irrigou_sem_efeito()
    regras.avaliar_reservatorio(ponte)
    assert "config" not in [c["alvo"] for c in comandos()]


def test_contrato_aceita_bloqueio_e_o_motivo_novo():
    c = contrato.validar_comando("config", "set", None, {"bombaBloqueada": 1})
    assert c["parametros"] == {"bombaBloqueada": 1}
    with pytest.raises(contrato.ErroContrato):
        contrato.validar_comando("config", "set", None, {"bombaBloqueada": 2})
    assert "bomba_bloqueada" in contrato.MOTIVOS
