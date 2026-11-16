import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("API_URL", "http://localhost:8000")


def chamar(metodo, caminho, corpo=None, token=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def ok(texto):
    print(f"OK    {texto}")


def falha(texto):
    print(f"FALHA {texto}")
    sys.exit(1)


def esperar(funcao, segundos, passo=1.0):
    fim = time.time() + segundos
    while time.time() < fim:
        resultado = funcao()
        if resultado:
            return resultado
        time.sleep(passo)
    return None


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "estacao-01"
    email, senha = os.environ.get("ADMIN_EMAIL"), os.environ.get("ADMIN_PASSWORD")
    if not email or not senha:
        falha("defina ADMIN_EMAIL e ADMIN_PASSWORD (os mesmos do .env)")

    status, _ = chamar("POST", "/api/comandos", {"dispositivoId": device, "alvo": "led", "duracaoS": 2})
    if status != 401:
        falha(f"comando sem login deveria dar 401, deu {status}")
    ok("comando sem login e recusado (401)")

    status, corpo = chamar("POST", "/api/login", {"email": email, "senha": senha})
    if status != 200 or corpo.get("papel") != "admin":
        falha(f"login do admin: {status} {corpo}")
    token = corpo["token"]
    ok("login do admin")

    def dispositivo():
        _, lista = chamar("GET", "/api/dispositivos", token=token)
        return next((d for d in lista if d["id"] == device), None)

    if not esperar(lambda: (dispositivo() or {}).get("online"), 30):
        falha(f"{device} nao ficou online em 30 s (o simulador/ESP32 esta rodando?)")
    ok(f"{device} aparece online")

    if not esperar(lambda: len((dispositivo() or {}).get("leituras", {})) >= 1, 30):
        falha(f"{device} sem leituras em 30 s")
    ok(f"{device} tem leituras reais")

    status, cmd = chamar("POST", "/api/comandos", {"dispositivoId": device, "alvo": "bomba", "acao": "on",
                                                    "duracaoS": 3}, token)
    if status != 201 or cmd.get("estado") != "enviado":
        falha(f"comando deveria ser 201 enviado: {status} {cmd}")
    cid = cmd["comando_id"]
    ok(f"comando {cid} registrado como enviado")

    def estado_final(c):
        _, atual = chamar("GET", f"/api/comandos/{c}", token=token)
        return atual if atual and atual["estado"] in ("confirmado", "rejeitado", "falhou", "expirado") else None

    final = esperar(lambda: estado_final(cid), 35)
    if not final or final["estado"] != "confirmado":
        falha(f"ciclo deveria terminar em confirmado: {final}")
    ok("ciclo terminou em confirmado")

    status, cmd2 = chamar("POST", "/api/comandos", {"dispositivoId": device, "alvo": "bomba", "acao": "on",
                                                     "duracaoS": 3}, token)
    if status != 201:
        falha(f"segundo comando: {status} {cmd2}")
    final2 = esperar(lambda: estado_final(cmd2["comando_id"]), 35)
    if not final2 or final2["estado"] != "rejeitado" or not final2.get("motivo"):
        falha(f"segundo comando imediato deveria ser rejeitado com motivo: {final2}")
    ok(f"segundo comando imediato rejeitado com motivo ({final2['motivo']})")

    print("TUDO OK")


if __name__ == "__main__":
    main()
