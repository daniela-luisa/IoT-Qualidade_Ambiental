import json
import logging
import uuid

import paho.mqtt.client as mqtt

log = logging.getLogger("macp.mqtt")


class PonteMQTT:
    def __init__(self, host: str, porta: int, prefixo: str, ao_receber):
        self.host = host
        self.porta = porta
        self.prefixo = prefixo
        self.ao_receber = ao_receber
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"macp-backend-{uuid.uuid4().hex[:8]}",
        )
        self.client.reconnect_delay_set(min_delay=1, max_delay=5)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def assinaturas(self) -> list[tuple[str, int]]:
        p = self.prefixo
        return [
            (f"{p}/+/telemetria/+", 0),
            (f"{p}/+/estado", 1),
            (f"{p}/+/status", 1),
            (f"{p}/+/confirmacao", 1),
        ]

    def iniciar(self) -> None:
        log.info("conectando ao broker %s:%s", self.host, self.porta)
        self.client.connect_async(self.host, self.porta, keepalive=30)
        self.client.loop_start()

    def parar(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    @property
    def conectado(self) -> bool:
        return self.client.is_connected()

    def publicar(self, topico: str, payload: dict, qos: int = 1, retain: bool = False) -> bool:
        if not self.conectado:
            return False
        info = self.client.publish(topico, json.dumps(payload, ensure_ascii=False), qos=qos, retain=retain)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("broker recusou a conexao: %s", reason_code)
            return
        client.subscribe(self.assinaturas())
        log.info("conectado ao broker; assinaturas refeitas")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("desconectado do broker (%s); reconectando sozinho", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            self.ao_receber(msg.topic, msg.payload)
        except Exception:
            log.exception("erro tratando %s", msg.topic)
