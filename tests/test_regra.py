"""
test_regra.py — 11 testes da regra de decisão automática (R8).

Cobre:
  - Disparo quando solo abaixo do limiar
  - Sem disparo quando solo acima ou igual ao limiar
  - Respeito ao intervalo mínimo entre disparos
  - Respeito ao modo seguro
  - Respeito ao dispositivo offline
  - Alerta solo_seco aberto ao disparar e resolvido ao recuperar
  - Sem disparo quando não há leituras na janela
  - decidir() do decisor legado (R10 / documentação)
"""

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

# ── helpers ────────────────────────────────────────────────────────────────


def ficar_online(device=D):
    publicar(device, "status", {"v": 1, "deviceId": device, "status": "online"})


def _set_modo(device: str, modo: str) -> None:
    """Atualiza o modo do dispositivo diretamente no banco."""
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


# ── testes ─────────────────────────────────────────────────────────────────


def test_solo_abaixo_do_limiar_dispara_bomba(ponte):
    """Média abaixo do limiar (30 %) → comando bomba enviado com origem='regra'."""
    ficar_online()
    _telemetria_solo(20.0)   # abaixo do limiar 30 %
    regras.avaliar_todas(ponte)
    c = _ultimo_estado_bomba()
    assert c is not None
    assert c["alvo"] == "bomba"
    assert c["acao"] == "on"
    assert c["origem"] == "regra"
    assert c["duracao_s"] == 5    # duração padrão da regra no schema
    assert len(ponte.publicados) == 1


def test_solo_acima_do_limiar_nao_dispara(ponte):
    """Média acima do limiar → nenhum comando."""
    ficar_online()
    _telemetria_solo(50.0)   # bem acima de 30 %
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_solo_igual_ao_limiar_nao_dispara(ponte):
    """Exatamente no limiar (30.0 %) → não dispara (seco é estritamente menor)."""
    ficar_online()
    _telemetria_solo(30.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_intervalo_minimo_impede_segundo_disparo(ponte):
    """Dois disparos seguidos sem esperar intervalo_min_s → só o primeiro é enviado."""
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 1

    # Segunda chamada imediata — intervalo não decorreu
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 1    # ainda só 1
    assert len(ponte.publicados) == 1


def test_intervalo_expirado_permite_novo_disparo(ponte):
    """Após forçar o enviado_em para o passado, o segundo disparo é permitido."""
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 1

    # Recua o timestamp do primeiro comando além do intervalo mínimo (60 s)
    consulta("UPDATE comandos SET enviado_em = now() - interval '90 seconds'")

    regras.avaliar_todas(ponte)
    assert _n_comandos() == 2
    assert len(ponte.publicados) == 2


def test_modo_seguro_nao_dispara(ponte):
    """Dispositivo em modo 'seguro' → regra é ignorada completamente."""
    ficar_online()
    _set_modo(D, "seguro")
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_dispositivo_offline_nao_dispara(ponte):
    """Dispositivo offline → broker não conseguiria entregar; regra ignorada."""
    # Não chamamos ficar_online() — dispositivo permanece offline
    _telemetria_solo(5.0)
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0
    assert ponte.publicados == []


def test_sem_leituras_na_janela_nao_dispara(ponte):
    """Sem dados de telemetria na janela de tempo → não pode decidir; ignora."""
    ficar_online()
    # Nenhuma telemetria publicada
    regras.avaliar_todas(ponte)
    assert _n_comandos() == 0


def test_alerta_solo_seco_aberto_ao_disparar(ponte):
    """Ao disparar a bomba, alerta solo_seco deve ser aberto."""
    ficar_online()
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    alertas = consulta("SELECT tipo, resolvido_em FROM alertas WHERE tipo = 'solo_seco'")
    assert len(alertas) == 1
    assert alertas[0]["resolvido_em"] is None


def test_alerta_solo_seco_resolvido_ao_recuperar(ponte):
    """Após solo seco, quando solo volta ao normal a regra deve resolver o alerta."""
    ficar_online()
    # 1. Dispara com solo seco
    _telemetria_solo(10.0)
    regras.avaliar_todas(ponte)
    assert len(consulta("SELECT id FROM alertas WHERE tipo = 'solo_seco' AND resolvido_em IS NULL")) == 1

    # 2. Publica telemetria com solo recuperado
    _telemetria_solo(60.0)
    regras.avaliar_todas(ponte)
    assert len(consulta("SELECT id FROM alertas WHERE tipo = 'solo_seco' AND resolvido_em IS NULL")) == 0


def test_regra_inativa_nao_dispara(ponte):
    """Regra com ativa=false não deve disparar mesmo com solo seco."""
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


# ── testes do decisor legado ────────────────────────────────────────────────


def test_decidir_inadequado():
    """Condição 'inadequado' aciona led e buzzer."""
    from decisor import decidir
    assert decidir("inadequado") == ("on", "on")


def test_decidir_atencao():
    """Condição 'atencao' aciona led mas não buzzer."""
    from decisor import decidir
    assert decidir("atencao") == ("on", "off")


def test_decidir_adequado_e_qualquer_outro():
    """Condição 'adequado' (e qualquer outro valor) desliga tudo."""
    from decisor import decidir
    assert decidir("adequado") == ("off", "off")
    assert decidir("desconhecido") == ("off", "off")
    assert decidir("") == ("off", "off")
