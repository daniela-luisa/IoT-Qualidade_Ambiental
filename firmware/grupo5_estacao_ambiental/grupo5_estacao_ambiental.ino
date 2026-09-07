/*
  Projeto IoT - Grupo 8 - Monitoramento Ambiental para Crescimento de Plantas

  Pinagem usada (ver documento de aprofundamento):
    DHT22 (temp/umidade do ar) -> GPIO14
    LDR (luminosidade)         -> GPIO34 (ADC1)
    Potenciometro (solo, sim.) -> GPIO35 (ADC1)
    Buzzer                     -> GPIO25
    LED (com resistor)         -> GPIO26

  Topicos MQTT:
    Publica:  grupo8/sensor/temperatura
              grupo8/sensor/umidade_ar
              grupo8/sensor/umidade_solo
              grupo8/sensor/luminosidade
              grupo8/status/led
              grupo8/status/buzzer
    Assina:   grupo8/comando/led      (payload: "on" ou "off")
              grupo8/comando/buzzer   (payload: "on" ou "off")

  Este e um esqueleto inicial para teste no simulador Wokwi.
  Ajustem os limiares de classificacao (SOIL_SECO, LIGHT_ESCURO etc.)
  depois de calibrar os sensores reais.
*/

#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// ---------- Configuracao de rede ----------
const char* WIFI_SSID     = "Wokwi-GUEST";   // rede virtual do Wokwi
const char* WIFI_PASSWORD = "";

const char* MQTT_BROKER = "broker.hivemq.com"; // broker publico de testes
const int   MQTT_PORT   = 1883;
const char* MQTT_CLIENT_ID = "grupo8-esp32";

// ---------- Pinos ----------
#define DHTPIN   14
#define DHTTYPE  DHT22
#define LDR_PIN  34
#define SOIL_PIN 35
#define BUZZER_PIN 25
#define LED_PIN    26

// ---------- Topicos ----------
const char* TOPIC_TEMP        = "grupo8/sensor/temperatura";
const char* TOPIC_UMID_AR     = "grupo8/sensor/umidade_ar";
const char* TOPIC_UMID_SOLO   = "grupo8/sensor/umidade_solo";
const char* TOPIC_LUMINOSIDADE = "grupo8/sensor/luminosidade";
const char* TOPIC_CMD_LED     = "grupo8/comando/led";
const char* TOPIC_CMD_BUZZER  = "grupo8/comando/buzzer";
const char* TOPIC_STATUS_LED    = "grupo8/status/led";
const char* TOPIC_STATUS_BUZZER = "grupo8/status/buzzer";

// ---------- Limiares (ajustar apos calibracao real) ----------
const int SOIL_SECO_MAX   = 1500;  // abaixo disso = solo seco (ADC bruto, 0-4095)
const int SOIL_ENCHARCADO = 3500;  // acima disso = solo encharcado
const int LIGHT_ESCURO_MAX = 1000; // abaixo disso = pouca luz (ADC bruto)

// ---------- Objetos globais ----------
DHT dht(DHTPIN, DHTTYPE);
WiFiClient espClient;
PubSubClient client(espClient);

unsigned long ultimaLeitura = 0;
const unsigned long INTERVALO_LEITURA = 5000; // 5 segundos

bool ledLigado = false;
bool buzzerLigado = false;

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
    client.publish(TOPIC_STATUS_LED, ledLigado ? "on" : "off");
  }

  if (String(topic) == TOPIC_CMD_BUZZER) {
    buzzerLigado = (mensagem == "on");
    digitalWrite(BUZZER_PIN, buzzerLigado ? HIGH : LOW);
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

  // Alerta automatico (sem depender so de comando manual)
  if (condicao == "inadequado") {
    digitalWrite(BUZZER_PIN, HIGH);
    digitalWrite(LED_PIN, HIGH);
    buzzerLigado = true;
    ledLigado = true;
  } else {
    digitalWrite(BUZZER_PIN, LOW);
    buzzerLigado = false;
  }
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

  unsigned long agora = millis();
  if (agora - ultimaLeitura >= INTERVALO_LEITURA) {
    ultimaLeitura = agora;
    lerEPublicarSensores();
  }
}
