import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app import db, regras
from conftest import consulta, publicar

pytestmark = pytest.mark.usefixtures("banco")

D = "estacao-01"
D2 = "estacao-02"


def ficar_online(device=D):
    publicar(device, "status", {"v": 1, "deviceId": device, "status": "online"})


def _set_modo(device: str, modo: str) -> None:
    with db.transacao() as conn:
        conn.exec_driver_sql(
            "UPDATE dispositivos SET modo = %(m)s WHERE id = %(d)s",
            {"m": modo, "d": device},
        )


def _set_online(device: str, online: bool) -> None:
    with db.transacao() as conn:
        conn.exec_driver_sql(
            "UPDATE dispositivos SET online = %(o)s WHERE id = %(d)s",
            {"o": online, "d": device},
        )


def _telemetria_solo(valor: float, device: str = D) -> None:
    publicar(device, "telemetria/umidade_solo", {"v": 1, "deviceId": device, "ts": None, "valor": valor})


def _n_comandos() -> int:
    return len(consulta("SELECT comando_id FROM comandos"))


def _ultimo_estado_bomba() -> dict | None:
    rows = consulta("SELECT alvo, acao, duracao_s, origem, estado FROM comandos ORDER BY enviado_em DESC LIMIT 1")
    return rows[0] if rows else None


def test_solo_abaixo_do_limiar_dispara_bomba(ponte):
    ficar_online()
    _telemetria_solo(20.0)
    regras.avaliar_todas(ponte)
    c = _ultimo_estado_bomba()
    assert c is not None
    assert c["alvo"] == "bomba"
    assert c["acao"] == "on"
    assert c["origem"] == "regra"
    assert c["duracao_s"] == 3
    assert len(ponte.publicados) == 1


def test_solo_acima_do_limiar_nao_dispara(ponte):
    ficar_online()
    _telemetria_solo(50.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_solo_igual_ao_limiar_nao_dispara(ponte):
    ficar_online()
    _telemetria_solo(30.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_intervalo_minimo_impede_segundo_disparo(ponte):
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 1

    regras.avaliar_todas(ponte)
    assert _n_comandos() == 1
    assert len(ponte.publicados) == 1


def test_intervalo_expirado_permite_novo_disparo(ponte):
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 1

    with db.transacao() as conn:
        conn.exec_driver_sql(
            "UPDATE comandos SET enviado_em = now() - interval '90 seconds'"
        )

    regras.avaliar_todas(ponte)
    assert _n_comandos() == 2
    assert len(ponte.publicados) == 2


def test_modo_seguro_nao_dispara(ponte):
    ficar_online()
    _set_modo(D, "seguro")
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_dispositivo_offline_nao_dispara(ponte):
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_sem_leituras_na_janela_nao_dispara(ponte):
    ficar_online()
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_alerta_solo_seco_aberto_ao_disparar(ponte):
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    alertas = consulta("SELECT tipo, resolvido_em FROM alertas WHERE tipo = 'solo_seco'")
    assert len(alertas) == 1
    assert alertas[0]["resolvido_em"] is None


def test_alerta_solo_seco_resolvido_ao_recuperar(ponte):
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    assert len(consulta("SELECT id FROM alertas WHERE tipo = 'solo_seco' AND resolvido_em IS NULL")) == 1

    _telemetria_solo(60.0)
    regras.avaliar_todas(ponte)
    assert len(consulta("SELECT id FROM alertas WHERE tipo = 'solo_seco' AND resolvido_em IS NULL")) == 0


def test_regra_inativa_nao_dispara(ponte):
    ficar_online()
    with db.transacao() as conn:
        conn.exec_driver_sql(
            "UPDATE regras SET ativa = false WHERE dispositivo_id = %(d)s",
            {"d": D},
        )
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_comando_rejeitado_tambem_respeita_intervalo(ponte):
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    [c] = consulta("SELECT comando_id FROM comandos")
    publicar(D, "confirmacao", {"v": 1, "commandId": c["comando_id"], "etapa": "recebido",
                                "resultado": "rejeitado", "motivo": "intertravamento_intervalo"})
    for _ in range(3):
        regras.avaliar_todas(ponte)
    assert _n_comandos() == 1
    assert len(ponte.publicados) == 1


def test_modo_manual_pausa_a_regra(ponte):
    ficar_online()
    _set_modo(D, "manual")
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_bomba_ja_ligada_nao_dispara(ponte):
    ficar_online()
    publicar(D, "estado", {"v": 1, "modo": "automatico", "bomba": "on"})
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_media_baixa_mas_ultima_leitura_recuperada_nao_dispara(ponte):
    ficar_online()
    for v in (5.0, 5.0, 5.0, 45.0):
        _telemetria_solo(v)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_alerta_solo_seco_abre_um_alerta_so(ponte):
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    with db.transacao() as conn:
        conn.exec_driver_sql("UPDATE comandos SET enviado_em = now() - interval '90 seconds'")
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 2
    [a] = consulta("SELECT tipo FROM alertas WHERE resolvido_em IS NULL")
    assert a["tipo"] == "solo_seco"


def test_decidir_inadequado():
    from decisor import decidir
    assert decidir("inadequado") == ("on", "on")


def test_decidir_atencao():
    from decisor import decidir
    assert decidir("atencao") == ("on", "off")


def test_decidir_adequado_e_qualquer_outro():
    from decisor import decidir
    assert decidir("adequado") == ("off", "off")
    assert decidir("desconhecido") == ("off", "off")
    assert decidir("") == ("off", "off")
