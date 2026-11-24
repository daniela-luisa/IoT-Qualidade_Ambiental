import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, comandos, contrato, db, handlers
from .config import config
from .mqtt_bridge import PonteMQTT

try:
    from . import regras
except ImportError:
    regras = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("macp.main")

ponte = None
_parar = threading.Event()


def _laco(nome: str, intervalo_s: float, funcao) -> threading.Thread:
    def rodar():
        while not _parar.wait(intervalo_s):
            try:
                funcao()
            except Exception:
                log.exception("erro no laco %s", nome)

    t = threading.Thread(target=rodar, name=nome, daemon=True)
    t.start()
    return t


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ponte
    if not config.jwt_secret:
        # sem segredo o token seria assinado com chave vazia: qualquer um forjaria um admin
        raise RuntimeError("JWT_SECRET vazio: defina no .env (32 ou mais caracteres)")
    if len(config.jwt_secret) < 32:
        log.warning("JWT_SECRET com menos de 32 caracteres: use um segredo mais longo")
    db.iniciar(config.db_url)
    db.esperar_banco()
    db.migrar()
    auth.garantir_usuarios_iniciais()
    ponte = PonteMQTT(config.mqtt_host, config.mqtt_port, config.mqtt_prefixo, handlers.tratar_mensagem)
    ponte.iniciar()
    _parar.clear()
    _laco("expirador", 5, comandos.expirar_pendentes)
    if regras is not None and hasattr(regras, "avaliar_todas"):
        _laco("regras", config.regras_intervalo_s, lambda: regras.avaliar_todas(ponte))
    log.info("backend no ar")
    yield
    _parar.set()
    ponte.parar()


app = FastAPI(title="MACP Grupo 5", version="1.0.0", lifespan=lifespan)


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str = ""):
        self.status, self.codigo, self.mensagem = status, codigo, mensagem


@app.exception_handler(ErroApi)
async def _erro_api(request: Request, erro: ErroApi):
    return JSONResponse(status_code=erro.status, content={"codigo": erro.codigo, "mensagem": erro.mensagem})


@app.exception_handler(StarletteHTTPException)
async def _erro_http(request: Request, erro: StarletteHTTPException):
    codigo = erro.detail if isinstance(erro.detail, str) else "erro"
    return JSONResponse(status_code=erro.status_code, content={"codigo": codigo, "mensagem": ""}, headers=erro.headers)


def exigir_admin(usuario: dict = Depends(auth.usuario_logado)) -> dict:
    if usuario.get("papel") != "admin":
        raise ErroApi(403, "sem_permissao", "apenas admin pode executar esta acao")
    return usuario


def _json(dados):
    return jsonable_encoder(dados)


def _checar_dispositivo(conn, dispositivo_id: str) -> None:
    if not db.linha(conn, "SELECT 1 AS ok FROM dispositivos WHERE id = :d", d=dispositivo_id):
        raise ErroApi(404, "dispositivo_desconhecido", dispositivo_id)


@app.get("/api/saude")
def saude():
    banco = db.banco_ok()
    mqtt = bool(ponte is not None and ponte.conectado)
    return {"status": "ok" if banco and mqtt else "degradado", "db": banco, "mqtt": mqtt}


@app.get("/api/dispositivos")
def listar_dispositivos(usuario: dict = Depends(auth.usuario_logado)):
    with db.transacao() as conn:
        dispositivos = db.linhas(
            conn,
            """
            SELECT id, nome, online, modo, condicao, fw, ultimo_contato, ultimo_estado, recursos
            FROM dispositivos ORDER BY id
            """,
        )
        # Uma busca no indice (dispositivo, grandeza, medido_em DESC) por par, em vez de
        # DISTINCT ON sobre a tabela inteira: a interface chama isto a cada 3 s e a tabela
        # ganha ~70 mil linhas por dia por estacao.
        ultimas = db.linhas(
            conn,
            """
            SELECT d.id AS dispositivo_id, g.grandeza, l.valor, l.unidade, l.medido_em
            FROM dispositivos d
            CROSS JOIN unnest(CAST(:grandezas AS text[])) AS g(grandeza)
            JOIN LATERAL (
                SELECT valor, unidade, medido_em FROM leituras
                WHERE dispositivo_id = d.id AND grandeza = g.grandeza
                ORDER BY medido_em DESC LIMIT 1
            ) l ON true
            """,
            grandezas=list(contrato.GRANDEZAS),
        )
    por_dispositivo: dict[str, dict] = {}
    for l in ultimas:
        por_dispositivo.setdefault(l.pop("dispositivo_id"), {})[l.pop("grandeza")] = l
    for d in dispositivos:
        d["leituras"] = por_dispositivo.get(d["id"], {})
        d["recursos_declarados"] = contrato.validar_recursos(d["recursos"]) is not None
        d["recursos"] = contrato.recursos_efetivos(d["recursos"])
    return _json(dispositivos)


@app.get("/api/leituras")
def listar_leituras(
    dispositivo_id: str,
    grandeza: str | None = None,
    minutos: int = Query(60, ge=1, le=10080),
    usuario: dict = Depends(auth.usuario_logado),
):
    if grandeza is not None and grandeza not in contrato.GRANDEZAS:
        raise ErroApi(422, "grandeza_invalida", grandeza)
    with db.transacao() as conn:
        _checar_dispositivo(conn, dispositivo_id)
        leituras = db.linhas(
            conn,
            """
            SELECT grandeza, valor, unidade, medido_em
            FROM leituras
            WHERE dispositivo_id = :d
              AND (CAST(:g AS text) IS NULL OR grandeza = :g)
              AND medido_em >= now() - make_interval(mins => :m)
            ORDER BY medido_em
            LIMIT 20000
            """,
            d=dispositivo_id,
            g=grandeza,
            m=minutos,
        )
    return _json(leituras)


def _media_movel(valores: list[float], janela: int = 5) -> list[float]:
    saida = []
    for i in range(len(valores)):
        trecho = valores[max(0, i - janela + 1): i + 1]
        saida.append(round(sum(trecho) / len(trecho), 2))
    return saida


@app.get("/api/painel")
def painel(
    dispositivo_id: str,
    horas: int = Query(1, ge=1, le=168),
    usuario: dict = Depends(auth.usuario_logado),
):
    bloco_s = horas * 60
    with db.transacao() as conn:
        _checar_dispositivo(conn, dispositivo_id)
        recursos = db.linha(conn, "SELECT recursos FROM dispositivos WHERE id = :d", d=dispositivo_id)["recursos"]
        blocos = db.linhas(
            conn,
            """
            SELECT grandeza,
                   to_timestamp(floor(extract(epoch FROM medido_em) / :b) * :b) AS bloco,
                   avg(valor) AS media
            FROM leituras
            WHERE dispositivo_id = :d AND medido_em >= now() - make_interval(hours => :h)
            GROUP BY grandeza, bloco
            ORDER BY grandeza, bloco
            """,
            d=dispositivo_id, h=horas, b=bloco_s,
        )
        resumo = db.linhas(
            conn,
            """
            SELECT grandeza, min(valor) AS minimo, max(valor) AS maximo, avg(valor) AS media,
                   count(*) AS n, max(unidade) AS unidade
            FROM leituras
            WHERE dispositivo_id = :d AND medido_em >= now() - make_interval(hours => :h)
            GROUP BY grandeza
            """,
            d=dispositivo_id, h=horas,
        )
        irrigacoes = db.linhas(
            conn,
            """
            SELECT comando_id, origem, duracao_s, enviado_em, confirmado_em
            FROM comandos
            WHERE dispositivo_id = :d AND alvo = 'bomba' AND acao = 'on' AND estado = 'confirmado'
              AND enviado_em >= now() - make_interval(hours => :h)
            ORDER BY enviado_em
            """,
            d=dispositivo_id, h=horas,
        )
        # Cada vez que a bomba LIGOU de fato (evento gerado pelo estado do firmware),
        # inclusive pela irrigacao local da placa, que nao passa pela tabela de comandos.
        acionamentos = db.linha(
            conn,
            """
            SELECT count(*) AS n FROM eventos
            WHERE dispositivo_id = :d AND tipo = 'bomba' AND detalhe LIKE 'on%'
              AND criado_em >= now() - make_interval(hours => :h)
            """,
            d=dispositivo_id, h=horas,
        )["n"]
        alertas_periodo = db.linhas(
            conn,
            """
            SELECT id, tipo, mensagem, aberto_em, resolvido_em
            FROM alertas
            WHERE dispositivo_id = :d AND aberto_em >= now() - make_interval(hours => :h)
            ORDER BY aberto_em
            """,
            d=dispositivo_id, h=horas,
        )

    grandezas = {}
    for g, (_, _, unidade) in contrato.GRANDEZAS.items():
        serie = [b for b in blocos if b["grandeza"] == g]
        valores = [round(b["media"], 2) for b in serie]
        r = next((x for x in resumo if x["grandeza"] == g), None)
        grandezas[g] = {
            "unidade": unidade,
            "serie": [{"t": b["bloco"], "valor": v} for b, v in zip(serie, valores)],
            "media_movel": [{"t": b["bloco"], "valor": v} for b, v in zip(serie, _media_movel(valores))],
            "minimo": round(r["minimo"], 2) if r else None,
            "maximo": round(r["maximo"], 2) if r else None,
            "media": round(r["media"], 2) if r else None,
            "n": r["n"] if r else 0,
        }
    return _json({
        "dispositivo_id": dispositivo_id,
        "recursos": contrato.recursos_efetivos(recursos),
        "horas": horas,
        "bloco_s": bloco_s,
        "grandezas": grandezas,
        "irrigacoes": len(irrigacoes),
        "irrigacoes_lista": irrigacoes,
        "acionamentos_bomba": acionamentos,
        "alertas": len(alertas_periodo),
        "alertas_lista": alertas_periodo,
    })


@app.get("/api/alertas")
def listar_alertas(
    ativos: bool = True,
    dispositivo_id: str | None = None,
    limite: int = Query(100, ge=1, le=1000),
    usuario: dict = Depends(auth.usuario_logado),
):
    with db.transacao() as conn:
        return _json(db.linhas(
            conn,
            """
            SELECT id, dispositivo_id, tipo, mensagem, aberto_em, resolvido_em
            FROM alertas
            WHERE (NOT :ativos OR resolvido_em IS NULL)
              AND (CAST(:d AS text) IS NULL OR dispositivo_id = :d)
            ORDER BY aberto_em DESC LIMIT :lim
            """,
            ativos=ativos, d=dispositivo_id, lim=limite,
        ))


@app.get("/api/eventos")
def listar_eventos(
    dispositivo_id: str | None = None,
    tipo: str | None = None,
    limite: int = Query(50, ge=1, le=1000),
    usuario: dict = Depends(auth.usuario_logado),
):
    with db.transacao() as conn:
        return _json(db.linhas(
            conn,
            """
            SELECT id, dispositivo_id, tipo, detalhe, criado_em
            FROM eventos
            WHERE (CAST(:d AS text) IS NULL OR dispositivo_id = :d)
              AND (CAST(:t AS text) IS NULL OR tipo = :t)
            ORDER BY criado_em DESC, id DESC LIMIT :lim
            """,
            d=dispositivo_id, t=tipo, lim=limite,
        ))


@app.get("/api/regras")
def listar_regras(usuario: dict = Depends(auth.usuario_logado)):
    with db.transacao() as conn:
        return _json(db.linhas(
            conn,
            """
            SELECT dispositivo_id, grandeza, limiar, janela_min, intervalo_min_s, duracao_s, ativa, atualizado_em
            FROM regras ORDER BY dispositivo_id
            """,
        ))


# campo, tipos aceitos, minimo, maximo (faixas do CONTRATOS.md)
_FAIXAS_REGRA = (
    ("limiar", (int, float), 10, 80),
    ("janela_min", int, 1, 120),
    ("intervalo_min_s", int, 30, 3600),
    ("duracao_s", int, 1, 15),
)


def _validar_regra(corpo: dict) -> dict:
    regra = {}
    for campo, tipos, minimo, maximo in _FAIXAS_REGRA:
        valor = corpo.get(campo)
        # bool e subclasse de int: True passaria como 1 sem o primeiro teste
        if isinstance(valor, bool) or not isinstance(valor, tipos) or not minimo <= valor <= maximo:
            tipo = "inteiro" if tipos is int else "numero"
            raise ErroApi(422, "valor_fora_da_faixa", f"{campo} deve ser {tipo} entre {minimo} e {maximo}")
        regra[campo] = valor
    if not isinstance(corpo.get("ativa"), bool):
        raise ErroApi(422, "valor_fora_da_faixa", "ativa deve ser true ou false")
    regra["ativa"] = corpo["ativa"]
    return regra


@app.put("/api/regras/{dispositivo_id}")
def salvar_regra(dispositivo_id: str, corpo: dict = Body(...), usuario: dict = Depends(exigir_admin)):
    regra = _validar_regra(corpo)
    with db.transacao() as conn:
        _checar_dispositivo(conn, dispositivo_id)
        salva = db.linha(
            conn,
            """
            INSERT INTO regras (dispositivo_id, limiar, janela_min, intervalo_min_s, duracao_s, ativa)
            VALUES (:d, :limiar, :janela_min, :intervalo_min_s, :duracao_s, :ativa)
            ON CONFLICT (dispositivo_id) DO UPDATE SET
                limiar = EXCLUDED.limiar,
                janela_min = EXCLUDED.janela_min,
                intervalo_min_s = EXCLUDED.intervalo_min_s,
                duracao_s = EXCLUDED.duracao_s,
                ativa = EXCLUDED.ativa,
                atualizado_em = now()
            RETURNING dispositivo_id, grandeza, limiar, janela_min, intervalo_min_s, duracao_s, ativa, atualizado_em
            """,
            d=dispositivo_id, **regra,
        )
        detalhe = ", ".join(f"{campo}={valor}" for campo, valor in regra.items())
        db.registrar_evento(conn, dispositivo_id, "regra_alterada", f"{usuario['email']}: {detalhe}")
    return _json(salva)


@app.get("/api/comandos")
def listar_comandos(
    dispositivo_id: str | None = None,
    limite: int = Query(50, ge=1, le=1000),
    usuario: dict = Depends(auth.usuario_logado),
):
    with db.transacao() as conn:
        return _json(db.linhas(
            conn,
            f"""
            SELECT {comandos.COLUNAS} FROM comandos
            WHERE (CAST(:d AS text) IS NULL OR dispositivo_id = :d)
            ORDER BY enviado_em DESC, comando_id DESC LIMIT :lim
            """,
            d=dispositivo_id, lim=limite,
        ))


@app.get("/api/comandos/{comando_id}")
def obter_comando(comando_id: str, usuario: dict = Depends(auth.usuario_logado)):
    with db.transacao() as conn:
        c = db.linha(conn, f"SELECT {comandos.COLUNAS} FROM comandos WHERE comando_id = :id", id=comando_id)
    if c is None:
        raise ErroApi(404, "comando_desconhecido", comando_id)
    return _json(c)


@app.post("/api/comandos", status_code=201)
def enviar_comando(corpo: dict = Body(...), usuario: dict = Depends(exigir_admin)):
    dispositivo_id = corpo.get("dispositivoId")
    if not isinstance(dispositivo_id, str) or not dispositivo_id:
        raise ErroApi(422, "valor_fora_da_faixa", "dispositivoId obrigatorio")
    try:
        comando = comandos.criar_comando(
            ponte,
            dispositivo_id,
            corpo.get("alvo"),
            acao=corpo.get("acao"),
            duracao=corpo.get("duracaoS"),
            parametros=corpo.get("parametros"),
            origem="usuario",
            usuario_id=usuario["id"],
        )
    except contrato.ErroContrato as e:
        raise ErroApi(422, e.codigo, e.detalhe) from None
    except comandos.DispositivoDesconhecido:
        raise ErroApi(404, "dispositivo_desconhecido", dispositivo_id) from None
    except comandos.DispositivoOffline:
        raise ErroApi(409, "dispositivo_offline", dispositivo_id) from None
    except comandos.BrokerIndisponivel as e:
        return JSONResponse(
            status_code=503,
            content={"codigo": "broker_indisponivel", "mensagem": "comando registrado como falhou",
                     "comando": _json(e.comando)},
        )
    return _json(comando)


class LoginEntrada(BaseModel):
    email: str
    senha: str


@app.post("/api/login")
def login(entrada: LoginEntrada):
    usuario = auth.autenticar(entrada.email.strip(), entrada.senha)
    if usuario is None:
        raise ErroApi(401, "credenciais_invalidas", "E-mail ou senha incorretos")
    return {"token": auth.criar_token(usuario), "papel": usuario["papel"], "email": usuario["email"]}


@app.get("/api/me")
def me(usuario: dict = Depends(auth.usuario_logado)):
    return usuario


if Path(config.web_dir).is_dir():
    app.mount("/", StaticFiles(directory=config.web_dir, html=True), name="web")
