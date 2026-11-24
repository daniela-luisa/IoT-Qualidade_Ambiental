import os
from dataclasses import dataclass
from pathlib import Path


def _texto(nome: str, padrao: str = "") -> str:
    return os.environ.get(nome, padrao).strip()


def _inteiro(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    return int(valor) if valor else padrao


def _web_padrao() -> str:
    if Path("/web").is_dir():
        return "/web"
    return str(Path(__file__).resolve().parents[2] / "web")


@dataclass(frozen=True)
class Config:
    db_url: str
    mqtt_host: str
    mqtt_port: int
    mqtt_prefixo: str
    comando_timeout_s: int
    fuso: str
    jwt_secret: str
    jwt_expira_min: int
    admin_email: str
    admin_password: str
    operador_email: str
    operador_password: str
    regras_intervalo_s: int
    web_dir: str


def montar_db_url() -> str:
    url = _texto("DATABASE_URL")
    if url:
        return url
    usuario = _texto("POSTGRES_USER", "macp")
    senha = _texto("POSTGRES_PASSWORD")
    host = _texto("DB_HOST", "localhost")
    porta = _inteiro("DB_PORT", 5432)
    banco = _texto("POSTGRES_DB", "macp")
    return f"postgresql+psycopg://{usuario}:{senha}@{host}:{porta}/{banco}"


def carregar() -> Config:
    return Config(
        db_url=montar_db_url(),
        mqtt_host=_texto("MQTT_HOST", "localhost"),
        mqtt_port=_inteiro("MQTT_PORT", 1883),
        mqtt_prefixo=_texto("MQTT_PREFIX", "grupo5"),
        comando_timeout_s=_inteiro("COMANDO_TIMEOUT_S", 30),
        fuso=_texto("TZ_APP", "America/Sao_Paulo"),
        jwt_secret=_texto("JWT_SECRET"),
        jwt_expira_min=_inteiro("JWT_EXPIRA_MIN", 60),
        admin_email=_texto("ADMIN_EMAIL"),
        admin_password=_texto("ADMIN_PASSWORD"),
        operador_email=_texto("OPERADOR_EMAIL"),
        operador_password=_texto("OPERADOR_PASSWORD"),
        regras_intervalo_s=_inteiro("REGRAS_INTERVALO_S", 15),
        web_dir=_texto("WEB_DIR") or _web_padrao(),
    )


config = carregar()
