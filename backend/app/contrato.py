import json
import re
from datetime import datetime, timezone

VERSAO = 1

GRANDEZAS = {
    "temperatura": (0.0, 50.0, "°C"),
    "umidade_ar": (20.0, 90.0, "%"),
    "umidade_solo": (0.0, 100.0, "%"),
    "luminosidade": (0.0, 100.0, "%"),
}

MODOS = {"automatico", "manual", "seguro"}
CONDICOES = {"adequado", "atencao", "inadequado"}
STATUS = {"online", "offline"}
EVENTOS_DISPOSITIVO = {"bomba", "modo", "condicao", "alarme"}

ATUADORES = {
    "bomba": (1, 15, 3),
    "led": (1, 60, 8),
    "buzzer": (1, 60, 8),
}

PARAMETROS_CONFIG = {
    "limiarSoloSecoPct": (10, 80),
    "intervaloMinS": (30, 3600),
    "duracaoMaxS": (1, 15),
    "bombaBloqueada": (0, 1),
}

ALVOS = set(ATUADORES) | {"config"}

MOTIVOS = {
    "intertravamento_intervalo",
    "intertravamento_duracao",
    "modo_seguro",
    "comando_duplicado",
    "alvo_invalido",
    "valor_fora_da_faixa",
    "json_invalido",
    "versao_nao_suportada",
    "bomba_bloqueada",
}

ETAPAS = {"recebido", "executado", "confirmado"}
RESULTADOS = {"ok", "rejeitado", "falhou"}

RE_DEVICE_ID = re.compile(r"^estacao-[0-9]{2}$")
RE_COMMAND_ID = re.compile(r"^c-[0-9]{8}-[0-9]{4,}$")

ANO_MINIMO_TS = 2024


class ErroContrato(Exception):
    def __init__(self, codigo: str, detalhe: str = ""):
        super().__init__(f"{codigo}: {detalhe}" if detalhe else codigo)
        self.codigo = codigo
        self.detalhe = detalhe


def ler_json(payload: bytes | str) -> dict:
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        dados = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as erro:
        raise ErroContrato("json_invalido", str(erro)[:120]) from None
    if not isinstance(dados, dict):
        raise ErroContrato("json_invalido", "payload nao e um objeto")
    if dados.get("v") != VERSAO or isinstance(dados.get("v"), bool):
        raise ErroContrato("versao_nao_suportada", f"v={dados.get('v')!r}")
    return dados


def device_id_valido(device_id) -> bool:
    return isinstance(device_id, str) and bool(RE_DEVICE_ID.match(device_id))


def interpretar_ts(valor, chegada: datetime) -> datetime:
    if not isinstance(valor, str) or not valor:
        return chegada
    try:
        ts = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return chegada
    if ts.tzinfo is None or ts.year < ANO_MINIMO_TS:
        return chegada
    return ts


def _numero(valor) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def validar_telemetria(grandeza: str, dados: dict) -> tuple[float, str]:
    if grandeza not in GRANDEZAS:
        raise ErroContrato("grandeza_invalida", grandeza)
    if "grandeza" in dados and dados["grandeza"] != grandeza:
        raise ErroContrato("grandeza_divergente", f"topico={grandeza} payload={dados['grandeza']}")
    minimo, maximo, unidade = GRANDEZAS[grandeza]
    valor = _numero(dados.get("valor"))
    if valor is None:
        raise ErroContrato("falha_sensor", f"{grandeza} valor={dados.get('valor')!r}")
    if not (minimo <= valor <= maximo):
        raise ErroContrato("falha_sensor", f"{grandeza} valor={valor} fora de {minimo:g} a {maximo:g}")
    return valor, unidade


def validar_estado(dados: dict) -> dict:
    modo = dados.get("modo")
    condicao = dados.get("condicao")
    fw = dados.get("fw")
    return {
        "modo": modo if modo in MODOS else None,
        "condicao": condicao if condicao in CONDICOES else None,
        "fw": fw if isinstance(fw, str) and fw else None,
        "recursos": validar_recursos(dados.get("recursos")),
    }


def validar_recursos(valor) -> dict | None:
    if not isinstance(valor, dict):
        return None
    sensores, atuadores = valor.get("sensores"), valor.get("atuadores")
    if not isinstance(sensores, list) or not isinstance(atuadores, list):
        return None
    return {
        "sensores": [g for g in GRANDEZAS if g in sensores],
        "atuadores": [a for a in ATUADORES if a in atuadores],
    }


def recursos_efetivos(valor) -> dict:
    declarados = validar_recursos(valor)
    if declarados is None:
        return {"sensores": list(GRANDEZAS), "atuadores": list(ATUADORES)}
    return declarados


def validar_status(dados: dict) -> str:
    status = dados.get("status")
    if status not in STATUS:
        raise ErroContrato("status_invalido", repr(status))
    return status


def validar_evento(dados: dict) -> tuple[str, str]:
    tipo = dados.get("tipo")
    if tipo not in EVENTOS_DISPOSITIVO:
        raise ErroContrato("evento_invalido", repr(tipo))
    detalhe = dados.get("detalhe")
    if not isinstance(detalhe, str) or not detalhe:
        raise ErroContrato("evento_invalido", f"detalhe={detalhe!r}")
    return tipo, detalhe[:200]


def validar_confirmacao(dados: dict) -> dict:
    comando_id = dados.get("commandId")
    if not isinstance(comando_id, str) or not RE_COMMAND_ID.match(comando_id):
        raise ErroContrato("command_id_invalido", repr(comando_id))
    etapa = dados.get("etapa")
    if etapa not in ETAPAS:
        raise ErroContrato("etapa_invalida", repr(etapa))
    resultado = dados.get("resultado", "ok")
    if resultado not in RESULTADOS:
        raise ErroContrato("resultado_invalido", repr(resultado))
    motivo = dados.get("motivo")
    if motivo is not None and not isinstance(motivo, str):
        motivo = str(motivo)
    return {
        "commandId": comando_id,
        "etapa": etapa,
        "resultado": resultado,
        "motivo": motivo,
        "motivo_no_catalogo": motivo is None or motivo in MOTIVOS,
    }


def estado_da_confirmacao(etapa: str, resultado: str) -> str:
    if resultado == "rejeitado":
        return "rejeitado"
    if resultado == "falhou":
        return "falhou"
    return etapa


def validar_comando(alvo, acao, duracao, parametros) -> dict:
    if alvo not in ALVOS:
        raise ErroContrato("alvo_invalido", repr(alvo))

    if alvo == "config":
        if acao not in (None, "set"):
            raise ErroContrato("valor_fora_da_faixa", f"acao={acao!r}")
        if not isinstance(parametros, dict) or not parametros:
            raise ErroContrato("valor_fora_da_faixa", "config sem parametros")
        normalizados = {}
        for nome, valor in parametros.items():
            if nome not in PARAMETROS_CONFIG:
                raise ErroContrato("valor_fora_da_faixa", f"parametro desconhecido {nome}")
            numero = _numero(valor)
            minimo, maximo = PARAMETROS_CONFIG[nome]
            if numero is None or not numero.is_integer() or not (minimo <= numero <= maximo):
                raise ErroContrato("valor_fora_da_faixa", f"{nome}={valor!r} deve ser inteiro de {minimo} a {maximo}")
            normalizados[nome] = int(numero)
        return {"alvo": alvo, "acao": "set", "duracaoS": None, "parametros": normalizados}

    if acao is None:
        acao = "on"
    if acao not in ("on", "off"):
        raise ErroContrato("valor_fora_da_faixa", f"acao={acao!r}")
    if acao == "off":
        return {"alvo": alvo, "acao": "off", "duracaoS": None, "parametros": None}

    minimo, maximo, padrao = ATUADORES[alvo]
    if duracao is None:
        duracao = padrao
    if isinstance(duracao, bool) or not isinstance(duracao, int) or not (minimo <= duracao <= maximo):
        raise ErroContrato("valor_fora_da_faixa", f"duracaoS={duracao!r} fora de {minimo} a {maximo}")
    return {"alvo": alvo, "acao": "on", "duracaoS": duracao, "parametros": None}


def formatar_command_id(data: datetime, numero: int) -> str:
    return f"c-{data:%Y%m%d}-{numero:04d}"


def montar_payload_comando(comando_id: str, ts: datetime, comando: dict, origem: str) -> dict:
    payload = {
        "v": VERSAO,
        "commandId": comando_id,
        "ts": ts.isoformat(timespec="seconds"),
        "alvo": comando["alvo"],
        "acao": comando["acao"],
    }
    if comando["duracaoS"] is not None:
        payload["duracaoS"] = comando["duracaoS"]
    if comando["parametros"]:
        payload.update(comando["parametros"])
    payload["origem"] = origem
    return payload


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)
