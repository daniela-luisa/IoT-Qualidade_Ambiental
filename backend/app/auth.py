import logging
from datetime import timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import contrato, db
from .config import config

log = logging.getLogger("macp.auth")

_bearer = HTTPBearer(auto_error=False)


def hash_senha(senha: str) -> str:
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verificar_senha(senha: str, senha_hash: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), senha_hash.encode("ascii"))
    except ValueError:
        return False


def criar_token(usuario: dict) -> str:
    agora = contrato.agora_utc()
    dados = {
        "sub": str(usuario["id"]),
        "email": usuario["email"],
        "papel": usuario["papel"],
        "iat": agora,
        "exp": agora + timedelta(minutes=config.jwt_expira_min),
    }
    return jwt.encode(dados, config.jwt_secret, algorithm="HS256")


def autenticar(email: str, senha: str) -> dict | None:
    with db.transacao() as conn:
        usuario = db.linha(conn, "SELECT id, email, senha_hash, papel FROM usuarios WHERE email = :e", e=email)
    if usuario is None or not verificar_senha(senha, usuario["senha_hash"]):
        return None
    return {"id": usuario["id"], "email": usuario["email"], "papel": usuario["papel"]}


def usuario_logado(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="nao_autenticado")
    try:
        dados = jwt.decode(cred.credentials, config.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="token_invalido") from None
    return {"id": int(dados["sub"]), "email": dados["email"], "papel": dados["papel"]}


def garantir_usuarios_iniciais() -> None:
    pares = [
        (config.admin_email, config.admin_password, "admin"),
        (config.operador_email, config.operador_password, "operador"),
    ]
    with db.transacao() as conn:
        for email, senha, papel in pares:
            if not email or not senha:
                log.warning("usuario %s nao configurado no .env", papel)
                continue
            criado = db.linha(
                conn,
                """
                INSERT INTO usuarios (email, senha_hash, papel) VALUES (:e, :h, :p)
                ON CONFLICT (email) DO NOTHING RETURNING id
                """,
                e=email,
                h=hash_senha(senha),
                p=papel,
            )
            if criado:
                log.info("usuario %s criado (%s)", email, papel)
