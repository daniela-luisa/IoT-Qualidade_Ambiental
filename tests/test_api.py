from conftest import ADMIN, consulta, publicar

D = "estacao-01"


def online(device=D):
    publicar(device, "status", {"v": 1, "deviceId": device, "status": "online"})


def test_saude_e_publica(cliente):
    r = cliente.get("/api/saude")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": True, "mqtt": True}


def test_saude_mostra_broker_fora(cliente, ponte):
    ponte.conectado = False
    assert cliente.get("/api/saude").json() == {"status": "degradado", "db": True, "mqtt": False}


def test_login_errado_e_401(cliente):
    r = cliente.post("/api/login", json={"email": ADMIN[0], "senha": "errada"})
    assert r.status_code == 401
    assert r.json()["codigo"] == "credenciais_invalidas"


def test_login_e_me(cliente, h_admin):
    r = cliente.get("/api/me", headers=h_admin)
    assert r.status_code == 200
    assert r.json()["papel"] == "admin" and r.json()["email"] == ADMIN[0]


def test_rotas_exigem_login(cliente):
    for rota in ["/api/me", "/api/dispositivos", "/api/leituras?dispositivo_id=estacao-01",
                 "/api/painel?dispositivo_id=estacao-01", "/api/alertas", "/api/eventos",
                 "/api/regras", "/api/comandos"]:
        assert cliente.get(rota).status_code == 401, rota
    assert cliente.get("/api/me", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_comando_sem_login_e_recusado(cliente, ponte):
    online()
    r = cliente.post("/api/comandos", json={"dispositivoId": D, "alvo": "led", "duracaoS": 3})
    assert r.status_code == 401
    assert ponte.publicados == []


def test_operador_ve_mas_nao_comanda(cliente, ponte, h_operador):
    online()
    assert cliente.get("/api/dispositivos", headers=h_operador).status_code == 200
    r = cliente.post("/api/comandos", headers=h_operador, json={"dispositivoId": D, "alvo": "led", "duracaoS": 3})
    assert r.status_code == 403
    assert ponte.publicados == []
    assert consulta("SELECT * FROM comandos") == []


def test_admin_envia_comando_publicado_no_topico_certo(cliente, ponte, h_admin):
    online()
    r = cliente.post("/api/comandos", headers=h_admin,
                     json={"dispositivoId": D, "alvo": "bomba", "acao": "on", "duracaoS": 5})
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert corpo["estado"] == "enviado" and corpo["origem"] == "usuario" and corpo["usuario_id"] is not None
    [(topico, payload, qos)] = ponte.publicados
    assert topico == f"grupo5/{D}/comando" and qos == 1
    assert payload["commandId"] == corpo["comando_id"]
    r2 = cliente.get(f"/api/comandos/{corpo['comando_id']}", headers=h_admin)
    assert r2.status_code == 200 and r2.json()["estado"] == "enviado"


def test_duracao_99_e_422_e_nao_publica(cliente, ponte, h_admin):
    online()
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "bomba", "duracaoS": 99})
    assert r.status_code == 422
    assert r.json()["codigo"] == "valor_fora_da_faixa"
    assert ponte.publicados == []
    assert consulta("SELECT * FROM comandos") == []


def test_alvo_invalido_e_422(cliente, h_admin):
    online()
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "ventilador"})
    assert r.status_code == 422 and r.json()["codigo"] == "alvo_invalido"


def test_dispositivo_desconhecido_e_404(cliente, h_admin):
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": "estacao-99", "alvo": "led"})
    assert r.status_code == 404


def test_dispositivo_offline_e_409(cliente, ponte, h_admin):
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": "estacao-02", "alvo": "led"})
    assert r.status_code == 409 and r.json()["codigo"] == "dispositivo_offline"
    assert ponte.publicados == []


def test_broker_fora_e_503_e_comando_fica_falhou(cliente, ponte, h_admin):
    online()
    ponte.conectado = False
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "led", "duracaoS": 2})
    assert r.status_code == 503 and r.json()["codigo"] == "broker_indisponivel"
    [c] = consulta("SELECT estado, motivo FROM comandos")
    assert c == {"estado": "falhou", "motivo": "broker_indisponivel"}


def test_broker_fora_tem_prioridade_sobre_offline(cliente, ponte, h_admin):
    ponte.conectado = False
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": "estacao-02", "alvo": "led"})
    assert r.status_code == 503 and r.json()["codigo"] == "broker_indisponivel"
    assert consulta("SELECT estado FROM comandos") == [{"estado": "falhou"}]


def test_comando_config_e_off(cliente, ponte, h_admin):
    online()
    r = cliente.post("/api/comandos", headers=h_admin,
                     json={"dispositivoId": D, "alvo": "config", "acao": "set", "parametros": {"intervaloMinS": 120}})
    assert r.status_code == 201, r.text
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "bomba", "acao": "off"})
    assert r.status_code == 201, r.text
    cfg, off = [p for _, p, _ in ponte.publicados]
    assert cfg["intervaloMinS"] == 120 and cfg["acao"] == "set"
    assert off["acao"] == "off" and "duracaoS" not in off


def test_dispositivos_com_ultima_leitura_de_cada_grandeza(cliente, h_operador):
    online()
    publicar(D, "telemetria/temperatura", {"v": 1, "ts": None, "valor": 22})
    publicar(D, "telemetria/temperatura", {"v": 1, "ts": None, "valor": 23})
    publicar(D, "telemetria/umidade_solo", {"v": 1, "ts": None, "valor": 40})
    publicar(D, "estado", {"v": 1, "modo": "automatico", "condicao": "adequado", "bomba": "off", "fw": "1.0.0"})
    r = cliente.get("/api/dispositivos", headers=h_operador)
    assert r.status_code == 200
    e1, e2 = r.json()
    assert (e1["id"], e2["id"]) == ("estacao-01", "estacao-02")
    assert e1["online"] is True and e1["modo"] == "automatico" and e1["fw"] == "1.0.0"
    assert e1["leituras"]["temperatura"]["valor"] == 23
    assert e1["leituras"]["umidade_solo"]["unidade"] == "%"
    assert e1["ultimo_estado"]["bomba"] == "off" and e1["ultimo_contato"]
    assert e2["online"] is False and e2["leituras"] == {}


def test_leituras_filtra_por_grandeza(cliente, h_operador):
    for v in (20, 21, 22):
        publicar(D, "telemetria/temperatura", {"v": 1, "ts": None, "valor": v})
    publicar(D, "telemetria/umidade_ar", {"v": 1, "ts": None, "valor": 50})
    r = cliente.get(f"/api/leituras?dispositivo_id={D}&grandeza=temperatura&minutos=10", headers=h_operador)
    assert [l["valor"] for l in r.json()] == [20, 21, 22]
    assert cliente.get(f"/api/leituras?dispositivo_id={D}&grandeza=pressao", headers=h_operador).status_code == 422


def test_painel_usa_dado_real_do_banco(cliente, ponte, h_admin):
    online()
    for v in (20, 30, 40):
        publicar(D, "telemetria/temperatura", {"v": 1, "ts": None, "valor": v})
    r = cliente.post("/api/comandos", headers=h_admin, json={"dispositivoId": D, "alvo": "bomba", "duracaoS": 3})
    cid = r.json()["comando_id"]
    publicar(D, "confirmacao", {"v": 1, "commandId": cid, "etapa": "executado", "resultado": "ok"})
    publicar(D, "estado", {"v": 1, "bomba": "on"})
    p = cliente.get(f"/api/painel?dispositivo_id={D}&horas=1", headers=h_admin).json()
    t = p["grandezas"]["temperatura"]
    assert (t["minimo"], t["maximo"], t["media"], t["n"]) == (20, 40, 30, 3)
    assert t["serie"] and len(t["serie"]) == len(t["media_movel"])
    assert p["irrigacoes"] == 1
    assert p["grandezas"]["umidade_ar"]["serie"] == []


def test_listas_de_alertas_eventos_regras_e_comandos(cliente, h_operador):
    online()
    publicar(D, "status", {"v": 1, "status": "offline"})
    alertas = cliente.get("/api/alertas", headers=h_operador).json()
    assert [a["tipo"] for a in alertas] == ["dispositivo_offline"]
    eventos = cliente.get("/api/eventos", headers=h_operador).json()
    assert {e["tipo"] for e in eventos} == {"status"}
    regras = cliente.get("/api/regras", headers=h_operador).json()
    assert [(r["dispositivo_id"], r["limiar"]) for r in regras] == [("estacao-01", 30), ("estacao-02", 30)]
    assert cliente.get("/api/comandos", headers=h_operador).json() == []
    assert cliente.get("/api/comandos/c-20260101-0001", headers=h_operador).status_code == 404


def test_senhas_ficam_so_como_hash(cliente):
    for u in consulta("SELECT senha_hash FROM usuarios"):
        assert u["senha_hash"].startswith("$2")
        assert "admin123" not in u["senha_hash"] and "operador123" not in u["senha_hash"]


REGRA = {"limiar": 35.5, "janela_min": 10, "intervalo_min_s": 120, "duracao_s": 7, "ativa": False}


def regra_atual(device=D):
    [r] = consulta(
        "SELECT limiar, janela_min, intervalo_min_s, duracao_s, ativa FROM regras WHERE dispositivo_id = :d",
        d=device,
    )
    return r


def eventos_regra():
    return consulta("SELECT dispositivo_id, detalhe FROM eventos WHERE tipo = 'regra_alterada'")


def test_admin_salva_regra_e_get_mostra(cliente, ponte, h_admin):
    r = cliente.put(f"/api/regras/{D}", headers=h_admin, json=REGRA)
    assert r.status_code == 200, r.text
    salva = r.json()
    assert {k: salva[k] for k in REGRA} == REGRA
    assert salva["dispositivo_id"] == D and salva["grandeza"] == "umidade_solo" and salva["atualizado_em"]
    regras = {x["dispositivo_id"]: x for x in cliente.get("/api/regras", headers=h_admin).json()}
    assert set(regras[D]) == set(salva)
    assert {k: regras[D][k] for k in REGRA} == REGRA
    assert regras["estacao-02"]["limiar"] == 30
    assert ponte.publicados == []


def test_operador_nao_altera_regra(cliente, h_operador):
    antes = regra_atual()
    r = cliente.put(f"/api/regras/{D}", headers=h_operador, json=REGRA)
    assert r.status_code == 403 and r.json()["codigo"] == "sem_permissao"
    assert regra_atual() == antes
    assert eventos_regra() == []


def test_regra_sem_login_e_401(cliente):
    antes = regra_atual()
    assert cliente.put(f"/api/regras/{D}", json=REGRA).status_code == 401
    assert regra_atual() == antes


def test_regra_fora_da_faixa_e_422(cliente, h_admin):
    antes = regra_atual()
    invalidos = [
        ("limiar", 9.9), ("limiar", 80.1), ("limiar", "30"), ("limiar", True),
        ("janela_min", 0), ("janela_min", 121), ("janela_min", 5.5), ("janela_min", True),
        ("intervalo_min_s", 29), ("intervalo_min_s", 3601), ("intervalo_min_s", "60"),
        ("duracao_s", 0), ("duracao_s", 16), ("duracao_s", True),
    ]
    for campo, valor in invalidos:
        r = cliente.put(f"/api/regras/{D}", headers=h_admin, json={**REGRA, campo: valor})
        assert r.status_code == 422, (campo, valor)
        assert r.json()["codigo"] == "valor_fora_da_faixa", (campo, valor)
        assert r.json()["mensagem"].startswith(campo), (campo, valor)
    for campo in REGRA:
        sem_campo = {k: v for k, v in REGRA.items() if k != campo}
        r = cliente.put(f"/api/regras/{D}", headers=h_admin, json=sem_campo)
        assert r.status_code == 422 and r.json()["codigo"] == "valor_fora_da_faixa", campo
    assert regra_atual() == antes
    assert eventos_regra() == []


def test_regra_aceita_os_limites_da_faixa(cliente, h_admin):
    for limites in ({"limiar": 10, "janela_min": 1, "intervalo_min_s": 30, "duracao_s": 1},
                    {"limiar": 80, "janela_min": 120, "intervalo_min_s": 3600, "duracao_s": 15}):
        r = cliente.put(f"/api/regras/{D}", headers=h_admin, json={**limites, "ativa": True})
        assert r.status_code == 200, r.text
        assert regra_atual() == {**limites, "ativa": True}


def test_regra_ativa_nao_bool_e_422(cliente, h_admin):
    for valor in (1, "true", None):
        r = cliente.put(f"/api/regras/{D}", headers=h_admin, json={**REGRA, "ativa": valor})
        assert r.status_code == 422 and r.json()["codigo"] == "valor_fora_da_faixa", valor
        assert r.json()["mensagem"].startswith("ativa"), valor


def test_regra_estacao_inexistente_e_404(cliente, h_admin):
    r = cliente.put("/api/regras/estacao-99", headers=h_admin, json=REGRA)
    assert r.status_code == 404 and r.json()["codigo"] == "dispositivo_desconhecido"
    assert eventos_regra() == []


def test_salvar_regra_gera_evento(cliente, h_admin):
    assert cliente.put(f"/api/regras/{D}", headers=h_admin, json=REGRA).status_code == 200
    [ev] = eventos_regra()
    assert ev["dispositivo_id"] == D
    assert ev["detalhe"].startswith(ADMIN[0])
    for trecho in ("limiar=35.5", "janela_min=10", "intervalo_min_s=120", "duracao_s=7", "ativa=False"):
        assert trecho in ev["detalhe"], trecho
