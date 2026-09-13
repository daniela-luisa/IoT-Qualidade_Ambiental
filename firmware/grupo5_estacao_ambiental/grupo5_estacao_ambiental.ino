/*
  Projeto IoT - Grupo 5 - Monitoramento Ambiental para Crescimento de Plantas

  Pinagem usada (ver documento de aprofundamento):
    DHT22 (temp/umidade do ar) -> GPIO14
    LDR (luminosidade)         -> GPIO34 (ADC1)
    Potenciometro (solo, sim.) -> GPIO35 (ADC1)
    Buzzer                     -> GPIO25
    LED (com resistor)         -> GPIO26

  Topicos MQTT:
    Publica:  grupo5/sensor/temperatura
              grupo5/sensor/umidade_ar
              grupo5/sensor/umidade_solo
              grupo5/sensor/luminosidade
              grupo5/status/led
              grupo5/status/buzzer
              grupo5/status/condicao
    Assina:   grupo5/comando/led      (payload: "on" ou "off")
              grupo5/comando/buzzer   (payload: "on" ou "off")

*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ---------- Configuracao de rede ----------
const char* WIFI_SSID     = "Wokwi-GUEST";   // rede virtual do Wokwi
const char* WIFI_PASSWORD = "";

const char* MQTT_BROKER = "broker.hivemq.com"; // broker publico de testes
const int   MQTT_PORT   = 1883;
const char* MQTT_CLIENT_ID = "grupo5-esp32";

// ---------- Pinos ----------
#define DHTPIN   14
#define DHTTYPE  DHT22
#define LDR_PIN  34
#define SOIL_PIN 35
#define BUZZER_PIN 25
#define LED_PIN    26

// ---------- Topicos ----------
const char* TOPIC_TEMP        = "grupo5/sensor/temperatura";
const char* TOPIC_UMID_AR     = "grupo5/sensor/umidade_ar";
const char* TOPIC_UMID_SOLO   = "grupo5/sensor/umidade_solo";
const char* TOPIC_LUMINOSIDADE = "grupo5/sensor/luminosidade";
const char* TOPIC_CMD_LED     = "grupo5/comando/led";
const char* TOPIC_CMD_BUZZER  = "grupo5/comando/buzzer";
const char* TOPIC_STATUS_LED    = "grupo5/status/led";
const char* TOPIC_STATUS_BUZZER = "grupo5/status/buzzer";
const char* TOPIC_STATUS_CONDICAO = "grupo5/status/condicao";

// ---------- Limiares (ajustar apos calibracao real) ----------
const int SOIL_SECO_MAX   = 1500;  // abaixo disso = solo seco (ADC bruto, 0-4095)
const int SOIL_ENCHARCADO = 3500;  // acima disso = solo encharcado
const int LIGHT_ESCURO_MAX = 1000; // abaixo disso = pouca luz (ADC bruto)

// ---------- Objetos globais ----------
DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

unsigned long ultimaLeitura = 0;
const unsigned long INTERVALO_LEITURA = 1000; // 1 segundo

bool ledLigado = false;
bool buzzerLigado = false;

// ---------- Controle do desligamento automatico ----------
unsigned long ledLigadoEm = 0;
unsigned long buzzerLigadoEm = 0;
const unsigned long DURACAO_ALERTA = 5000; // 5 segundos ligado, ajustar se quiser

// ---------- Wi-Fi ----------
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

// ---------- Callback de comandos MQTT ----------
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
    if (ledLigado) ledLigadoEm = millis();
    client.publish(TOPIC_STATUS_LED, ledLigado ? "on" : "off");
  }

  if (String(topic) == TOPIC_CMD_BUZZER) {
    buzzerLigado = (mensagem == "on");
    if (buzzerLigado) {
      tone(BUZZER_PIN, 1000); // gera um tom de 1000Hz
      buzzerLigadoEm = millis();
    } else {
      noTone(BUZZER_PIN);
    }
    client.publish(TOPIC_STATUS_BUZZER, buzzerLigado ? "on" : "off");
  }
}

// ---------- Reconexao MQTT ----------
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

// ---------- Classificacao simples da condicao da planta ----------
String classificarCondicao(int soloRaw, int luzRaw, float temperatura) {
  bool soloRuim = (soloRaw < SOIL_SECO_MAX) || (soloRaw > SOIL_ENCHARCADO);
  bool poucaLuz = (luzRaw < LIGHT_ESCURO_MAX);
  bool tempRuim = (temperatura > 32.0 || temperatura < 10.0);

  if (soloRuim || tempRuim) {
    return "inadequado";
  } else if (poucaLuz) {
    return "atencao";
  }
  return "adequado";
}

// ---------- Leitura, validacao, classificacao e publicacao ----------
void lerEPublicarSensores() {
  float temperatura = dht.readTemperature();
  float umidadeAr    = dht.readHumidity();
  int soloRaw = analogRead(SOIL_PIN);
  int luzRaw  = analogRead(LDR_PIN);

  // Validacao basica da leitura do DHT22
  if (isnan(temperatura) || isnan(umidadeAr)) {
    Serial.println("Falha na leitura do DHT22 - descartando esta amostra.");
    return;
  }

  String condicao = classificarCondicao(soloRaw, luzRaw, temperatura);

  Serial.println("---- Leitura dos sensores ----");
  Serial.print("Temperatura: ");   Serial.print(temperatura); Serial.println(" C");
  Serial.print("Umidade do ar: "); Serial.print(umidadeAr);   Serial.println(" %");
  Serial.print("Umidade do solo (raw): "); Serial.println(soloRaw);
  Serial.print("Luminosidade (raw): ");    Serial.println(luzRaw);
  Serial.print("Condicao classificada: "); Serial.println(condicao);

  client.publish(TOPIC_TEMP, String(temperatura).c_str());
  client.publish(TOPIC_UMID_AR, String(umidadeAr).c_str());
  client.publish(TOPIC_UMID_SOLO, String(soloRaw).c_str());
  client.publish(TOPIC_LUMINOSIDADE, String(luzRaw).c_str());

  // A decisao de acionar o atuador nao acontece mais aqui.
  // O firmware so publica a classificacao; quem decide ligar o
  // LED/buzzer a partir dela e algo de fora, pelo MQTT (ver nota
  // de arquitetura no topo do arquivo).
  client.publish(TOPIC_STATUS_CONDICAO, condicao.c_str());
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
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

  // Desliga o LED sozinho apos DURACAO_ALERTA
  if (ledLigado && millis() - ledLigadoEm >= DURACAO_ALERTA) {
    ledLigado = false;
    digitalWrite(LED_PIN, LOW);
    client.publish(TOPIC_STATUS_LED, "off");
  }

  // Desliga o buzzer sozinho apos DURACAO_ALERTA
  if (buzzerLigado && millis() - buzzerLigadoEm >= DURACAO_ALERTA) {
    buzzerLigado = false;
    noTone(BUZZER_PIN);
    client.publish(TOPIC_STATUS_BUZZER, "off");
  }

  unsigned long agora = millis();
  if (agora - ultimaLeitura >= INTERVALO_LEITURA) {
    ultimaLeitura = agora;
    lerEPublicarSensores();
  }
}