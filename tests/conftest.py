import json
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

os.environ.setdefault("JWT_SECRET", "segredo-ficticio-de-teste-com-mais-de-32-caracteres")
from app import auth, db

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

ADMIN = ("admin@teste.local", "admin123")
OPERADOR = ("operador@teste.local", "operador123")


class PonteFalsa:
    prefixo = "grupo5"

    def __init__(self):
        self.conectado = True
        self.publicados: list[tuple[str, dict, int]] = []

    def publicar(self, topico, payload, qos=1, retain=False):
        if not self.conectado:
            return False
        self.publicados.append((topico, payload, qos))
        return True


@pytest.fixture(scope="session")
def _engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL nao definido")
    nome_banco = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
    assert "test" in nome_banco, "TEST_DATABASE_URL precisa apontar para um banco de teste (ex.: macp_test)"
    return db.iniciar(TEST_DATABASE_URL)


@pytest.fixture
def banco(_engine):
    schema = (RAIZ / "db" / "schema.sql").read_text(encoding="utf-8")
    bruto = _engine.raw_connection()
    try:
        cur = bruto.cursor()
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute(schema)
        bruto.commit()
    finally:
        bruto.close()

    with db.transacao() as conn:
        for (email, senha), papel in ((ADMIN, "admin"), (OPERADOR, "operador")):
            conn.exec_driver_sql(
                "INSERT INTO usuarios (email, senha_hash, papel) VALUES (%(e)s, %(h)s, %(p)s)",
                {"e": email, "h": auth.hash_senha(senha), "p": papel},
            )
    return _engine


@pytest.fixture
def ponte(monkeypatch):
    from app import main

    falsa = PonteFalsa()
    monkeypatch.setattr(main, "ponte", falsa)
    return falsa


@pytest.fixture
def cliente(banco, ponte):
    from fastapi.testclient import TestClient

    from app import main

    return TestClient(main.app)


def _token(cliente, email, senha):
    r = cliente.post("/api/login", json={"email": email, "senha": senha})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def h_admin(cliente):
    return _token(cliente, *ADMIN)


@pytest.fixture
def h_operador(cliente):
    return _token(cliente, *OPERADOR)


def publicar(device: str, sufixo: str, dados) -> None:
    from app import handlers

    payload = dados if isinstance(dados, (bytes, str)) else json.dumps(dados)
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    handlers.tratar_mensagem(f"grupo5/{device}/{sufixo}", payload)


def consulta(sql: str, **params):
    with db.transacao() as conn:
        return db.linhas(conn, sql, **params)
