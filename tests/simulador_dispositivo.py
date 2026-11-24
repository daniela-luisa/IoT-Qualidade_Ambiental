import argparse
import json
import os
import random
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

ALVOS = {"bomba": (1, 15), "led": (1, 60), "buzzer": (1, 60)}
FAIXAS_CONFIG = {"limiarSoloSecoPct": (10, 80), "intervaloMinS": (30, 3600), "duracaoMaxS": (1, 15)}


def agora_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class Estacao:
    def __init__(self, args):
        self.id = args.device
        self.prefixo = f"{args.prefixo}/{self.id}"
        self.intervalo = args.intervalo
        self.modo = args.modo
        self.fw = args.fw
        self.seco = args.seco
        self.falhar_sensor = args.falha_sensor
        self.tem_bomba = not args.sem_bomba
        self.atuadores = {"bomba": "off", "led": "off", "buzzer": "off"} if self.tem_bomba else {"led": "off", "buzzer": "off"}
        self.config = {"limiarSoloSecoPct": 30, "intervaloMinS": args.intervalo_min_bomba, "duracaoMaxS": 15}
        self.ultima_irrigacao = 0.0
        self.vistos: set[str] = set()
        self.solo = 20.0 if args.seco else 55.0
        self.temp = 24.0
        self.ar = 60.0
        self.luz = 70.0
        self.trava = threading.Lock()

        self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{self.id}-{random.randint(0, 9999)}")
        self.c.will_set(f"{self.prefixo}/status", json.dumps({"v": 1, "deviceId": self.id, "status": "offline"}),
                        qos=1, retain=True)
        self.c.reconnect_delay_set(1, 5)
        self.c.on_connect = self.on_connect
        self.c.on_message = self.on_message
        self.c.connect_async(args.host, args.port, keepalive=10)

    def pub(self, sufixo, dados, qos=1, retain=False):
        dados = {"v": 1, "deviceId": self.id, **dados}
        self.c.publish(f"{self.prefixo}/{sufixo}", json.dumps(dados, ensure_ascii=False), qos=qos, retain=retain)

    def condicao(self):
        if self.solo < self.config["limiarSoloSecoPct"] or self.temp > 38:
            return "inadequado"
        if self.solo < self.config["limiarSoloSecoPct"] + 10 or self.luz < 20:
            return "atencao"
        return "adequado"

    def publicar_estado(self):
        recursos = {"sensores": ["temperatura", "umidade_ar", "umidade_solo", "luminosidade"],
                    "atuadores": list(self.atuadores)}
        self.pub("estado", {"ts": agora_iso(), "modo": self.modo, "condicao": self.condicao(),
                            **self.atuadores, "fw": self.fw, "recursos": recursos}, qos=1, retain=True)

    def publicar_telemetria(self):
        alvo_solo = 20.0 if self.seco else 55.0
        if self.atuadores.get("bomba") == "on":
            alvo_solo = 80.0
        self.solo += (alvo_solo - self.solo) * 0.3 + random.uniform(-1, 1)
        self.temp += random.uniform(-0.3, 0.3)
        self.ar += random.uniform(-1, 1)
        self.luz += random.uniform(-2, 2)
        leituras = {
            "temperatura": (round(min(max(self.temp, 0), 50), 1), "°C"),
            "umidade_ar": (round(min(max(self.ar, 20), 90), 1), "%"),
            "umidade_solo": (round(min(max(self.solo, 0), 100), 1), "%"),
            "luminosidade": (round(min(max(self.luz, 0), 100), 1), "%"),
        }
        for grandeza, (valor, unidade) in leituras.items():
            if self.falhar_sensor and grandeza == "temperatura":
                valor = None
            self.pub(f"telemetria/{grandeza}", {"ts": agora_iso(), "grandeza": grandeza, "valor": valor,
                                                "unidade": unidade}, qos=0)

    def confirmar(self, cid, etapa, resultado="ok", motivo=None):
        self.pub("confirmacao", {"commandId": cid, "ts": agora_iso(), "etapa": etapa,
                                 "resultado": resultado, "motivo": motivo})

    def on_message(self, client, userdata, msg):
        try:
            cmd = json.loads(msg.payload)
        except ValueError:
            print("[sim] comando com JSON invalido, sem commandId para responder")
            return
        cid = cmd.get("commandId")
        print(f"[sim] comando recebido: {cmd}")
        with self.trava:
            motivo = self.validar(cmd)
            if motivo:
                self.confirmar(cid, "recebido", "rejeitado", motivo)
                print(f"[sim] rejeitado: {motivo}")
                return
            self.vistos.add(cid)
            self.confirmar(cid, "recebido")
            self.executar(cmd)
            self.confirmar(cid, "executado")
            self.publicar_estado()

    def validar(self, cmd):
        if cmd.get("v") != 1:
            return "versao_nao_suportada"
        if cmd.get("commandId") in self.vistos:
            return "comando_duplicado"
        alvo, acao = cmd.get("alvo"), cmd.get("acao")
        if alvo == "config":
            for nome, (lo, hi) in FAIXAS_CONFIG.items():
                if nome in cmd and not (lo <= cmd[nome] <= hi):
                    return "valor_fora_da_faixa"
            return None
        if alvo not in ALVOS or alvo not in self.atuadores:
            return "alvo_invalido"
        if acao == "off":
            return None
        duracao = cmd.get("duracaoS")
        lo, hi = ALVOS[alvo]
        if acao != "on" or not isinstance(duracao, int) or not (lo <= duracao <= hi):
            return "valor_fora_da_faixa"
        if alvo == "bomba":
            if self.modo == "seguro":
                return "modo_seguro"
            if duracao > self.config["duracaoMaxS"]:
                return "intertravamento_duracao"
            if time.monotonic() - self.ultima_irrigacao < self.config["intervaloMinS"]:
                return "intertravamento_intervalo"
        return None

    def executar(self, cmd):
        alvo = cmd["alvo"]
        if alvo == "config":
            for nome in FAIXAS_CONFIG:
                if nome in cmd:
                    self.config[nome] = cmd[nome]
            print(f"[sim] config aplicada: {self.config}")
            return
        if cmd["acao"] == "off":
            self.atuadores[alvo] = "off"
            return
        self.atuadores[alvo] = "on"
        if alvo == "bomba":
            self.ultima_irrigacao = time.monotonic()
        threading.Timer(cmd["duracaoS"], self.desligar, args=(alvo,)).start()

    def desligar(self, alvo):
        with self.trava:
            self.atuadores[alvo] = "off"
            self.publicar_estado()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            return
        client.subscribe(f"{self.prefixo}/comando", qos=1)
        self.pub("status", {"status": "online"}, qos=1, retain=True)
        self.publicar_estado()
        print(f"[sim] {self.id} conectado e online")

    def rodar(self):
        self.c.loop_start()
        try:
            while True:
                if self.c.is_connected():
                    self.publicar_telemetria()
                time.sleep(self.intervalo)
        except KeyboardInterrupt:
            pass
        finally:
            self.pub("status", {"status": "offline"}, qos=1, retain=True)
            time.sleep(0.3)
            self.c.loop_stop()
            self.c.disconnect()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", default="estacao-01")
    p.add_argument("--host", default=os.environ.get("MQTT_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "1883")))
    p.add_argument("--prefixo", default=os.environ.get("MQTT_PREFIX", "grupo5"))
    p.add_argument("--intervalo", type=float, default=5, help="segundos entre telemetrias")
    p.add_argument("--intervalo-min-bomba", type=int, default=60, help="intertravamento entre irrigacoes (s)")
    p.add_argument("--modo", default="automatico", choices=["automatico", "manual", "seguro"])
    p.add_argument("--fw", default="1.0.0")
    p.add_argument("--seco", action="store_true", help="mantem o solo perto de 20%%")
    p.add_argument("--sem-bomba", action="store_true", help="estacao sem rele/bomba (so LED e buzzer)")
    p.add_argument("--falha-sensor", action="store_true", help="temperatura sai com valor nulo")
    Estacao(p.parse_args()).rodar()


if __name__ == "__main__":
    main()
