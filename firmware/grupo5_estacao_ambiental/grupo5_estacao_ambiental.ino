#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include "secrets.h" 

const char* MQTT_BROKER = "broker.hivemq.com";
const int MQTT_PORT = 1883;
const char* MQTT_CLIENT_ID = "grupo5-esp32";

#define DHTPIN 14
#define DHTTYPE DHT11
#define LDR_PIN 34
#define SOIL_PIN 35
#define BUZZER_PIN 25
#define LED_PIN 26

const char* TOPIC_TEMP = "grupo5/sensor/temperatura";
const char* TOPIC_UMID_AR = "grupo5/sensor/umidade_ar";
const char* TOPIC_UMID_SOLO = "grupo5/sensor/umidade_solo";
const char* TOPIC_LUMINOSIDADE = "grupo5/sensor/luminosidade";
const char* TOPIC_CMD_LED = "grupo5/comando/led";
const char* TOPIC_CMD_BUZZER = "grupo5/comando/buzzer";
const char* TOPIC_STATUS_LED = "grupo5/status/led";
const char* TOPIC_STATUS_BUZZER = "grupo5/status/buzzer";
const char* TOPIC_STATUS_CONDICAO = "grupo5/status/condicao";

DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

unsigned long ultimaLeitura = 0;
const unsigned long INTERVALO_LEITURA = 5000;

bool ledLigado = false;
bool buzzerLigado = false;

unsigned long ledLigadoEm = 0;
unsigned long buzzerLigadoEm = 0;
const unsigned long DURACAO_ALERTA = 8000;

void conectarWiFi() {
  Serial.print("Conectando ao Wi-Fi");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.print("Wi-Fi conectado. IP: ");
  Serial.println(WiFi.localIP());
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String mensagem;

  for (unsigned int i = 0; i < length; i++) {
    mensagem += (char)payload[i];
  }

  mensagem.trim();
  mensagem.toLowerCase();

  Serial.print("Comando recebido em [");
  Serial.print(topic);
  Serial.print("]: ");
  Serial.println(mensagem);

  if (String(topic) == TOPIC_CMD_LED) {
    ledLigado = (mensagem == "on");

    digitalWrite(LED_PIN, ledLigado ? HIGH : LOW);

    if (ledLigado) {
      ledLigadoEm = millis();
    }

    client.publish(
      TOPIC_STATUS_LED,
      ledLigado ? "on" : "off"
    );
  }

  if (String(topic) == TOPIC_CMD_BUZZER) {
    buzzerLigado = (mensagem == "on");

    if (buzzerLigado) {
      tone(BUZZER_PIN, 1000);
      buzzerLigadoEm = millis();
    } else {
      noTone(BUZZER_PIN);
    }

    client.publish(
      TOPIC_STATUS_BUZZER,
      buzzerLigado ? "on" : "off"
    );
  }
}

void reconectarMQTT() {
  while (!client.connected()) {
    Serial.print("Conectando ao broker MQTT...");

    if (client.connect(MQTT_CLIENT_ID)) {
      Serial.println(" conectado!");

      client.subscribe(TOPIC_CMD_LED);
      client.subscribe(TOPIC_CMD_BUZZER);
    } else {
      Serial.print(" falhou, rc=");
      Serial.print(client.state());
      Serial.println(" tentando novamente em 3s");

      delay(3000);
    }
  }
}

String classificarCondicao(
  bool soloSeco,
  bool poucaLuz,
  float temperatura
) {
  bool tempRuim =
    (temperatura > 32.0 || temperatura < 10.0);

  if (soloSeco || tempRuim) {
    return "inadequado";
  }

  if (poucaLuz) {
    return "atencao";
  }

  return "adequado";
}

void lerEPublicarSensores() {
  float temperatura = dht.readTemperature();
  float umidadeAr = dht.readHumidity();

  int soloDigital = digitalRead(SOIL_PIN);
  int luzDigital = digitalRead(LDR_PIN);

  bool soloSeco = (soloDigital == HIGH);
  bool poucaLuz = (luzDigital == LOW);

  if (isnan(temperatura) || isnan(umidadeAr)) {
    Serial.println("Falha na leitura do DHT11.");
    return;
  }

  String condicao = classificarCondicao(
    soloSeco,
    poucaLuz,
    temperatura
  );

  Serial.println("---- Leitura dos sensores ----");

  Serial.print("Temperatura: ");
  Serial.print(temperatura);
  Serial.println(" C");

  Serial.print("Umidade do ar: ");
  Serial.print(umidadeAr);
  Serial.println(" %");

  Serial.print("Umidade do solo: ");

  if (soloSeco) {
    Serial.println("SECO");
  } else {
    Serial.println("UMIDO");
  }

  Serial.print("Estado digital do solo: ");
  Serial.println(soloDigital);

  Serial.print("Luminosidade: ");

  if (poucaLuz) {
    Serial.println("ESCURO");
  } else {
    Serial.println("CLARO");
  }

  Serial.print("Estado digital da luminosidade: ");
  Serial.println(luzDigital);

  Serial.print("Condicao classificada: ");
  Serial.println(condicao);

  client.publish(
    TOPIC_TEMP,
    String(temperatura).c_str()
  );

  client.publish(
    TOPIC_UMID_AR,
    String(umidadeAr).c_str()
  );

  client.publish(
    TOPIC_UMID_SOLO,
    soloSeco ? "seco" : "umido"
  );

  client.publish(
    TOPIC_LUMINOSIDADE,
    poucaLuz ? "escuro" : "claro"
  );

  client.publish(
    TOPIC_STATUS_CONDICAO,
    condicao.c_str()
  );
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LDR_PIN, INPUT);
  pinMode(SOIL_PIN, INPUT);

  dht.begin();

  conectarWiFi();

  client.setServer(MQTT_BROKER, MQTT_PORT);
  client.setCallback(mqttCallback);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi caiu, reconectando...");
    conectarWiFi();
  }

  if (!client.connected()) {
    reconectarMQTT();
  }

  client.loop();

  if (
    ledLigado &&
    millis() - ledLigadoEm >= DURACAO_ALERTA
  ) {
    ledLigado = false;
    digitalWrite(LED_PIN, LOW);
    client.publish(TOPIC_STATUS_LED, "off");
  }

  if (
    buzzerLigado &&
    millis() - buzzerLigadoEm >= DURACAO_ALERTA
  ) {
    buzzerLigado = false;
    noTone(BUZZER_PIN);
    client.publish(TOPIC_STATUS_BUZZER, "off");
  }

  unsigned long agora = millis();

  if (
    agora - ultimaLeitura >= INTERVALO_LEITURA
  ) {
    ultimaLeitura = agora;
    lerEPublicarSensores();
  }
}