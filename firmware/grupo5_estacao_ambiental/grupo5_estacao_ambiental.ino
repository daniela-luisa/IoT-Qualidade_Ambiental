// Estacao ambiental - Grupo 5 - N3 (arquivo unico para o Wokwi). Contrato MQTT v1.
// fw 1.2.1: rele acionado em open-drain no prototipo fisico (modulo de 5 V nao soltava com o HIGH de 3,3 V do ESP32).
//
// LIGACAO DA BOMBA (prototipo fisico):
//   ESP 3V3 -> VCC do rele | ESP GND -> GND do rele | GPIO 27 -> IN do rele (direto, sem diodo)
//   Fonte 5V (+) -> COM | NO -> bomba (+) | bomba (-) -> Fonte (-) | NC vazio
//   Diodo 1N4007 em paralelo com a bomba (faixa no +). A bomba NAO usa o 5V do ESP.
#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <Preferences.h>
#include <time.h>
#include <ArduinoJson.h>

// ===================== CONFIGURACAO =====================
// MODO_SIMULADOR 1 = Wokwi | 0 = prototipo fisico. Nos dois casos o broker e o Mosquitto do
// docker compose (porta 1883 do notebook), o mesmo que o backend assina.
//   Wokwi: o ESP32 simulado alcanca o notebook pelo nome host.wokwi.internal. Funciona no
//          Wokwi para VS Code; no site wokwi.com so com o Private IoT Gateway ligado.
//   Fisico: criar secrets.h (fora do git) com WIFI_SSID, WIFI_PASSWORD, MQTT_HOST (IP do
//          notebook) e, opcional, OTA_PASSWORD. No modo simulador o secrets.h e ignorado.
#define MODO_SIMULADOR 0

#define DEVICE_ID   "estacao-01"      // a segunda placa usa "estacao-02"
#define FW_VERSION  "1.4.0"
// 1 = esta placa tem rele + bomba. 0 = estacao SEM bomba: rejeita comando de bomba (alvo_invalido),
// nao irriga sozinha e avisa o backend pelo campo "recursos" do estado. Com o solo seco, o backend
// passa a acionar LED + buzzer nela para alguem regar na mao.
#define TEM_BOMBA   1

// Igual ao MQTT_PREFIX do .env do backend (padrao "grupo5"). Com prefixo diferente o backend nao ve nada.
#define MQTT_PREFIX "grupo5"
#define MQTT_PORT   1883

#if MODO_SIMULADOR
  #define WIFI_SSID         "Wokwi-GUEST"          // unica rede que o ESP32 simulado enxerga
  #define WIFI_PASSWORD     ""
  #define MQTT_HOST         "host.wokwi.internal"  // = o notebook (Mosquitto do docker compose)
  #define DHT_TIPO          DHT22     // Wokwi so tem DHT22
  #define RELE_ATIVO_BAIXO  0         // rele do Wokwi liga em ALTO (diagram.json: bomba no contato NO)
  #define MQTT_KEEPALIVE_S  60        // Wokwi roda mais devagar que o tempo real
  #define USAR_OTA          0
  #define USAR_NTP          0         // relogio simulado atrasa; ts vai null e o backend usa a hora de chegada
#else
  #if __has_include("secrets.h")
    #include "secrets.h"
  #endif
  #ifndef WIFI_SSID
    #define WIFI_SSID       "TDESI"
    #define WIFI_PASSWORD   "leo12345"
  #endif
  #ifndef MQTT_HOST
    #define MQTT_HOST       "10.252.1.58"   // IP do notebook na rede local (macOS: ipconfig getifaddr en0)
  #endif
  #define DHT_TIPO          DHT11
  #define RELE_ATIVO_BAIXO  1         // modulo do grupo liga em BAIXO (IN solto = desligado, IN em GND = ligado)
  #define MQTT_KEEPALIVE_S  15
  #define USAR_OTA          1
  #define USAR_NTP          1
#endif
// Wokwi (photoresistor-sensor) e os modulos LDR comuns: mais luz = tensao MENOR no AO.
#define LDR_INVERTIDO     1

#define PIN_DHT     14
#define PIN_SOLO    35
#define PIN_LDR     34
#define PIN_LED     26
#define PIN_BUZZER  25
#define PIN_RELE    27

// calibracao do solo (ADC 0..4095; ALTO = seco). Medir no sensor real: no ar (SECO) e na agua (MOLHADO).
#define SOLO_ADC_SECO     3200
#define SOLO_ADC_MOLHADO  1200
// Sensor de solo "sendo lido": leitura bruta abaixo do minimo = fio AO solto, sensor sem VCC ou curto com GND.
// Sem teto: no ar o sensor satura perto de 4095, e isso e SOLO SECO (leitura valida), nao falha.
// Sem solo valido a bomba NAO liga (o comando e rejeitado como modo_seguro) e a telemetria vai null.
#define SOLO_ADC_MIN_VALIDO  100
#define SOLO_ADC_MAX_VALIDO  4095
// 1 = a bomba so liga com o sensor de solo lendo (padrao, usar sempre). 0 = SO para testar o rele na bancada.
#define EXIGIR_SOLO_VALIDO   1
// 1 = a propria placa liga a bomba quando o solo fica abaixo de limiarSoloSecoPct (so em modo automatico,
// respeitando intervaloMinS e duracaoMaxS). 0 = so o backend liga a bomba.
#define IRRIGACAO_LOCAL      1

#define PERIODO_TELEMETRIA_MS  5000UL
#define PERIODO_ESTADO_MS      30000UL
#define RECONEXAO_MQTT_MS      5000UL
#define PISCA_ALERTA_MS        500UL
#define FUSO_SEGUNDOS          (-3 * 3600)
#if USAR_OTA
  #include <ArduinoOTA.h>
#endif
// ========================================================

// ===================== LOGICA (sem hardware) =====================
// logica.h - regras da estacao SEM dependencia de hardware (compila no PC com g++).
// Tudo que decide "pode ou nao pode" mora aqui: intertravamento da bomba, modos,
// validacao de comando, duplicados, e a montagem dos JSON do contrato MQTT v1.
// O .ino so liga isto aos pinos e ao MQTT.
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <time.h>


namespace g5 {

enum Modo : uint8_t { AUTOMATICO = 0, MANUAL = 1, SEGURO = 2 };
inline const char* nomeModo(Modo m) { return m == AUTOMATICO ? "automatico" : (m == MANUAL ? "manual" : "seguro"); }

// ---- constantes do contrato v1 ----
constexpr int BOMBA_DUR_MIN = 1, BOMBA_DUR_MAX = 15, BOMBA_DUR_PADRAO = 5;
constexpr int LED_DUR_MIN = 1, LED_DUR_MAX = 60, LED_DUR_PADRAO = 8;
constexpr uint32_t MANUAL_TIMEOUT_MS = 60000UL;
constexpr int FALHAS_PARA_SEGURO = 3;  // leituras seguidas do DHT com erro
constexpr int N_VISTOS = 16;

// ref ligeiramente no futuro (agora lido antes do evento) nao conta como passado; funciona com estouro do millis().
inline bool passou(uint32_t agora, uint32_t ref, uint32_t ms) { int32_t d = (int32_t)(agora - ref); return d >= 0 && (uint32_t)d >= ms; }

struct Cfg {
  int limiarSoloSecoPct = 30;  // 10..80
  int intervaloMinS = 60;      // 30..3600
  int duracaoMaxS = 15;        // 1..15
};

// Resultado de processar um comando. O .ino publica "recebido" e, se executado, "executado".
struct Resposta {
  char commandId[40] = "?";
  bool aceito = false;
  const char* motivo = nullptr;  // so quando aceito == false
  bool estadoMudou = false;      // publicar estado
  bool cfgMudou = false;         // gravar na NVS
};

class Estacao {
 public:
  Cfg cfg;
  Modo modo = SEGURO;  // boot em seguro; a primeira leitura valida do DHT passa para automatico
  bool bomba = false, led = false, buzzer = false;
  bool temBomba = true;    // false = estacao sem rele/bomba (TEM_BOMBA 0)
  bool exigirSolo = true;  // bomba so liga com o sensor de solo lendo
  bool soloOk = false;     // ultima leitura do solo foi valida

  // chamar a cada loop(). Retorna true se algo mudou (desligou atuador, mudou de modo).
  bool tick(uint32_t agora) {
    bool mudou = false;
    if (bomba) {
      int lim = durBombaS < cfg.duracaoMaxS ? durBombaS : cfg.duracaoMaxS;  // teto vale mesmo se a config baixar no meio
      if (passou(agora, bombaIni, (uint32_t)lim * 1000UL)) { bomba = false; mudou = true; }
    }
    if (led && passou(agora, ledIni, (uint32_t)durLedS * 1000UL)) { led = false; mudou = true; }
    if (buzzer && passou(agora, buzzerIni, (uint32_t)durBuzzerS * 1000UL)) { buzzer = false; mudou = true; }
    if (modo == MANUAL && passou(agora, manualDesde, MANUAL_TIMEOUT_MS)) { modo = AUTOMATICO; mudou = true; }
    return mudou;
  }

  // chamar a cada ciclo de leitura. Retorna true se algo mudou (modo ou bomba desligada).
  // Falha do DHT OU do solo (com exigirSolo) conta como falha de sensor: 3 seguidas = modo seguro.
  bool registrarLeituraSensores(bool dhtOk, bool soloValido) {
    soloOk = soloValido;
    bool mudou = false;
    if (exigirSolo && !soloValido && bomba) { bomba = false; mudou = true; }  // perdeu o solo: desliga na hora
    bool ok = dhtOk && (soloValido || !exigirSolo);
    if (ok) {
      falhasSeguidas = 0;
      if (modo == SEGURO) { modo = AUTOMATICO; return true; }
      return mudou;
    }
    if (falhasSeguidas < 1000) falhasSeguidas++;
    if (falhasSeguidas >= FALHAS_PARA_SEGURO && modo != SEGURO) {
      modo = SEGURO;
      bomba = false;  // seguro = bomba desligada na hora
      return true;
    }
    return mudou;
  }

  // Irrigacao automatica local: so em AUTOMATICO (manual = pessoa no controle, seguro = nunca),
  // com solo valido abaixo do limiar e fora do intervalo minimo desde a ultima irrigacao.
  // Liga por BOMBA_DUR_PADRAO s (limitado por duracaoMaxS); o tick() desliga sozinho.
  bool irrigarSeSeco(float soloPct, uint32_t agora) {
    if (!temBomba || modo != AUTOMATICO || bomba) return false;
    if (exigirSolo && !soloOk) return false;
    if (isnan(soloPct) || soloPct >= (float)cfg.limiarSoloSecoPct) return false;
    if (jaIrrigou && !passou(agora, bombaIni, (uint32_t)cfg.intervaloMinS * 1000UL)) return false;
    int dur = BOMBA_DUR_PADRAO < cfg.duracaoMaxS ? BOMBA_DUR_PADRAO : cfg.duracaoMaxS;
    bomba = true; bombaIni = agora; durBombaS = dur; jaIrrigou = true;
    return true;
  }

  Resposta comando(const char* payload, size_t tam, uint32_t agora) {
    Resposta r;
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, payload, tam);
    if (err || !doc.is<JsonObject>()) return rejeitar(r, "json_invalido");
    const char* cid = doc["commandId"] | "";
    if (cid[0]) { strncpy(r.commandId, cid, sizeof(r.commandId) - 1); r.commandId[sizeof(r.commandId) - 1] = 0; }

    if (!(doc["v"].is<int>() && doc["v"].as<int>() == 1)) return rejeitar(r, "versao_nao_suportada");
    if (cid[0]) {
      if (jaVisto(r.commandId)) return rejeitar(r, "comando_duplicado");
      lembrar(r.commandId);
    }

    const char* alvo = doc["alvo"] | "";
    const char* acao = doc["acao"] | "";
    const char* origem = doc["origem"] | "";
    bool ehConfig = strcmp(alvo, "config") == 0;
    bool ehAtuador = strcmp(alvo, "bomba") == 0 || strcmp(alvo, "led") == 0 || strcmp(alvo, "buzzer") == 0;

    if (!ehConfig && !ehAtuador) return rejeitar(r, "alvo_invalido");
    if (!temBomba && strcmp(alvo, "bomba") == 0) return rejeitar(r, "alvo_invalido");  // atuador que esta placa nao tem
    // modo seguro: aceita config e DESLIGAR atuador (os dois sao sempre seguros); ligar e rejeitado
    if (modo == SEGURO && ehAtuador && strcmp(acao, "off") != 0) return rejeitar(r, "modo_seguro");
    // sem leitura valida do solo a bomba nao liga (motivo do contrato: modo_seguro)
    if (exigirSolo && !soloOk && strcmp(alvo, "bomba") == 0 && strcmp(acao, "on") == 0) return rejeitar(r, "modo_seguro");

    if (ehConfig) return aplicarConfig(doc, r);

    bool ligar = strcmp(acao, "on") == 0;
    if (!ligar && strcmp(acao, "off") != 0) return rejeitar(r, "valor_fora_da_faixa");
    bool ehBomba = strcmp(alvo, "bomba") == 0;

    int dur = 0;
    if (ligar) {
      int padrao = ehBomba ? BOMBA_DUR_PADRAO : LED_DUR_PADRAO;
      JsonVariantConst d = doc["duracaoS"];
      if (d.isNull()) dur = padrao;
      else if (d.is<int>()) dur = d.as<int>();
      else return rejeitar(r, "valor_fora_da_faixa");
      if (ehBomba) {
        // fora da faixa do contrato (1..15) e valor_fora_da_faixa; dentro dela mas acima do limite configurado e intertravamento
        if (dur < BOMBA_DUR_MIN || dur > BOMBA_DUR_MAX) return rejeitar(r, "valor_fora_da_faixa");
        if (dur > cfg.duracaoMaxS) return rejeitar(r, "intertravamento_duracao");
        if (bomba || (jaIrrigou && !passou(agora, bombaIni, (uint32_t)cfg.intervaloMinS * 1000UL)))
          return rejeitar(r, "intertravamento_intervalo");
      } else if (dur < LED_DUR_MIN || dur > LED_DUR_MAX) {
        return rejeitar(r, "valor_fora_da_faixa");
      }
    }

    r.aceito = true;
    r.estadoMudou = true;
    if (ehBomba) {
      if (ligar) { bomba = true; bombaIni = agora; durBombaS = dur; jaIrrigou = true; } else bomba = false;
    } else if (strcmp(alvo, "led") == 0) {
      if (ligar) { led = true; ledIni = agora; durLedS = dur; } else led = false;
    } else {
      if (ligar) { buzzer = true; buzzerIni = agora; durBuzzerS = dur; } else buzzer = false;
    }
    // comando de pessoa (origem diferente de "regra") assume o controle: modo manual por 60 s
    if (strcmp(origem, "regra") != 0 && modo != SEGURO) {
      modo = MANUAL;
      manualDesde = agora;
    }
    return r;
  }

 private:
  uint32_t bombaIni = 0, ledIni = 0, buzzerIni = 0, manualDesde = 0;
  int durBombaS = 0, durLedS = 0, durBuzzerS = 0;
  bool jaIrrigou = false;
  int falhasSeguidas = 0;
  char vistos[N_VISTOS][40] = {};
  int proximoVisto = 0;

  static Resposta& rejeitar(Resposta& r, const char* motivo) { r.aceito = false; r.motivo = motivo; return r; }

  bool jaVisto(const char* id) const {
    for (int i = 0; i < N_VISTOS; i++) if (vistos[i][0] && strcmp(vistos[i], id) == 0) return true;
    return false;
  }
  void lembrar(const char* id) {
    strncpy(vistos[proximoVisto], id, 39);
    vistos[proximoVisto][39] = 0;
    proximoVisto = (proximoVisto + 1) % N_VISTOS;
  }

  Resposta aplicarConfig(JsonDocument& doc, Resposta& r) {
    struct Item { const char* nome; int minimo, maximo; int* destino; };
    Item itens[3] = {{"limiarSoloSecoPct", 10, 80, &cfg.limiarSoloSecoPct},
                     {"intervaloMinS", 30, 3600, &cfg.intervaloMinS},
                     {"duracaoMaxS", 1, 15, &cfg.duracaoMaxS}};
    int novos[3];
    int presentes = 0;
    for (int i = 0; i < 3; i++) {
      JsonVariantConst v = doc[itens[i].nome];
      novos[i] = *itens[i].destino;
      if (v.isNull()) continue;
      if (!v.is<int>() || v.as<int>() < itens[i].minimo || v.as<int>() > itens[i].maximo) return rejeitar(r, "valor_fora_da_faixa");
      novos[i] = v.as<int>();
      presentes++;
    }
    if (presentes == 0) return rejeitar(r, "valor_fora_da_faixa");
    for (int i = 0; i < 3; i++) *itens[i].destino = novos[i];  // tudo ou nada
    r.aceito = true;
    r.cfgMudou = true;
    r.estadoMudou = true;
    return r;
  }
};

// ---------------- conversao de sensores ----------------
inline float limitar(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

// raw ADC 0..4095. Sensor de solo: valor ALTO = seco. secoRaw/molhadoRaw vem da calibracao (config.h).
inline float pctSolo(int raw, int secoRaw, int molhadoRaw) {
  if (secoRaw == molhadoRaw) return 0;
  return limitar((float)(secoRaw - raw) * 100.0f / (float)(secoRaw - molhadoRaw), 0, 100);
}
inline float pctLuz(int raw, bool invertido) {
  float p = limitar((float)raw * 100.0f / 4095.0f, 0, 100);
  return invertido ? 100.0f - p : p;
}
// temp pode ser NAN (falha do DHT): so entra na regra se valida.
inline const char* condicao(float soloPct, float temp, float luzPct, int limiarSolo) {
  bool tempRuim = !isnan(temp) && (temp > 32.0f || temp < 10.0f);
  if (isnan(soloPct) || soloPct < (float)limiarSolo || tempRuim) return "inadequado";  // solo sem leitura = inadequado
  if (luzPct < 20.0f) return "atencao";
  return "adequado";
}

// ---------------- JSON do contrato v1 ----------------
// ts vazio ("") vira null (sem NTP). Todas devolvem o tamanho escrito.
inline size_t jsonTelemetria(char* buf, size_t n, const char* dev, const char* ts, const char* grandeza, float valor, const char* unidade) {
  JsonDocument d;
  d["v"] = 1; d["deviceId"] = dev;
  if (ts[0]) d["ts"] = ts; else d["ts"] = nullptr;
  d["grandeza"] = grandeza;
  if (isnan(valor)) d["valor"] = nullptr; else d["valor"] = roundf(valor * 10.0f) / 10.0f;
  d["unidade"] = unidade;
  return serializeJson(d, buf, n);
}
inline size_t jsonEstado(char* buf, size_t n, const char* dev, const char* ts, const Estacao& e, const char* cond, const char* fw) {
  JsonDocument d;
  d["v"] = 1; d["deviceId"] = dev;
  if (ts[0]) d["ts"] = ts; else d["ts"] = nullptr;
  d["modo"] = nomeModo(e.modo); d["condicao"] = cond;
  if (e.temBomba) d["bomba"] = e.bomba ? "on" : "off";  // sem bomba: o campo nao vai
  d["led"] = e.led ? "on" : "off"; d["buzzer"] = e.buzzer ? "on" : "off";
  d["fw"] = fw;
  // o que esta placa tem: o backend e a interface se ajustam a isto
  JsonObject rec = d["recursos"].to<JsonObject>();
  JsonArray sens = rec["sensores"].to<JsonArray>();
  sens.add("temperatura"); sens.add("umidade_ar"); sens.add("umidade_solo"); sens.add("luminosidade");
  JsonArray atu = rec["atuadores"].to<JsonArray>();
  if (e.temBomba) atu.add("bomba");
  atu.add("led"); atu.add("buzzer");
  d["limiarSoloSecoPct"] = e.cfg.limiarSoloSecoPct; d["intervaloMinS"] = e.cfg.intervaloMinS; d["duracaoMaxS"] = e.cfg.duracaoMaxS;
  return serializeJson(d, buf, n);
}
inline size_t jsonConfirmacao(char* buf, size_t n, const char* dev, const char* cid, const char* ts, const char* etapa, const char* resultado, const char* motivo) {
  JsonDocument d;
  d["v"] = 1; d["deviceId"] = dev; d["commandId"] = cid;
  if (ts[0]) d["ts"] = ts; else d["ts"] = nullptr;
  d["etapa"] = etapa; d["resultado"] = resultado;
  if (motivo) d["motivo"] = motivo; else d["motivo"] = nullptr;
  return serializeJson(d, buf, n);
}
inline size_t jsonStatus(char* buf, size_t n, const char* dev, bool online) {
  JsonDocument d;
  d["v"] = 1; d["deviceId"] = dev; d["status"] = online ? "online" : "offline";
  return serializeJson(d, buf, n);
}

// "2026-11-24T09:15:00-03:00" (strftime %z da "-0300"; o contrato quer com dois pontos)
inline void formatarTs(char* buf, size_t n, const struct tm& t) {
  char z[8] = "";
  strftime(buf, n, "%Y-%m-%dT%H:%M:%S", &t);
  strftime(z, sizeof(z), "%z", &t);
  size_t l = strlen(buf);
  if (strlen(z) == 5 && l + 7 <= n) snprintf(buf + l, n - l, "%.3s:%.2s", z, z + 3);
}

}  // namespace g5

// ===================== PROGRAMA PRINCIPAL =====================
DHT dht(PIN_DHT, DHT_TIPO);
WiFiClient wifi;
PubSubClient mqtt(wifi);
Preferences prefs;
g5::Estacao est;

char T_STATUS[80], T_ESTADO[80], T_COMANDO[80], T_CONF[80], T_TELE[80];
char clientId[48];
const char* condAtual = "indefinida";
uint32_t ultLeitura = 0, ultEstado = 0, ultTentativaMqtt = 0;
bool primeiraLeitura = true, ntpIniciado = false, falhaForcada = false;
bool ledAplicado = false, buzzerAplicado = false, releAplicado = false;
float ultSolo = NAN, ultLuz = NAN, ultTemp = NAN;
// regra local (R2): sem broker e com condicao inadequada (ou em modo seguro) o LED pisca sozinho.
// Bomba: com IRRIGACAO_LOCAL 1 a placa irriga sozinha quando o solo esta seco (ver irrigarSeSeco);
// comandos do backend continuam valendo (on/off) e um comando de pessoa pausa a regra por 60 s (modo manual).
bool alertaLocal = false, ledPisca = false;
uint32_t ultPisca = 0;

// ------------------------------------------------------------ util
void agoraTs(char* buf, size_t n) {
  buf[0] = 0;
  time_t t = time(nullptr);
  if (t < 1700000000) return;  // sem NTP: ts = null, o backend usa a hora de chegada
  struct tm info;
  localtime_r(&t, &info);
  g5::formatarTs(buf, n, info);
}

void pub(const char* topico, const char* payload, bool retain) {
  if (!mqtt.connected()) return;
  mqtt.publish(topico, payload, retain);  // PubSubClient publica em QoS 0 (ver README do firmware)
}

void publicarEstado() {
  char ts[32], buf[512];
  agoraTs(ts, sizeof ts);
  g5::jsonEstado(buf, sizeof buf, DEVICE_ID, ts, est, condAtual, FW_VERSION);
  pub(T_ESTADO, buf, true);
}

void publicarConfirmacao(const char* cid, const char* etapa, const char* resultado, const char* motivo) {
  char ts[32], buf[300];
  agoraTs(ts, sizeof ts);
  g5::jsonConfirmacao(buf, sizeof buf, DEVICE_ID, cid, ts, etapa, resultado, motivo);
  pub(T_CONF, buf, false);
}

void publicarTelemetria(const char* grandeza, float valor, const char* unidade) {
  char ts[32], buf[250], topico[96];
  agoraTs(ts, sizeof ts);
  g5::jsonTelemetria(buf, sizeof buf, DEVICE_ID, ts, grandeza, valor, unidade);
  snprintf(topico, sizeof topico, "%s/%s", T_TELE, grandeza);
  pub(topico, buf, false);
}

// ------------------------------------------------------------ atuadores (so aqui se mexe nos pinos)
// Rele fisico (ativo em BAIXO, modulo de 5 V): o pino nunca vai para HIGH.
//   ligar    -> pino puxa para GND (corrente no opto = rele fecha COM-NO)
//   desligar -> pino solto (sem corrente no opto = rele abre). Com HIGH de 3,3 V num modulo
//               de 5 V sobrava tensao no LED do opto e o rele nao soltava: bomba sempre ligada.
// Wokwi (ativo em ALTO): saida normal push-pull.
void releInit() {
#if RELE_ATIVO_BAIXO
  pinMode(PIN_RELE, INPUT);                // pino solto = desligado (nunca HIGH de 3,3 V)
#else
  digitalWrite(PIN_RELE, LOW);
  pinMode(PIN_RELE, OUTPUT);
  digitalWrite(PIN_RELE, LOW);
#endif
}

void releFisico(bool ligar) {
#if RELE_ATIVO_BAIXO
  if (ligar) { digitalWrite(PIN_RELE, LOW); pinMode(PIN_RELE, OUTPUT); digitalWrite(PIN_RELE, LOW); }  // GND = liga
  else       { pinMode(PIN_RELE, INPUT); }                                                           // solto = desliga
#else
  digitalWrite(PIN_RELE, ligar ? HIGH : LOW);
#endif
}

void aplicarSaidas() {
  bool releLigar = est.bomba;
  if (releLigar != releAplicado) {
    releFisico(releLigar);
    releAplicado = releLigar;
    Serial.printf("[bomba] %s\n", releLigar ? "LIGADA" : "desligada");
  }
  bool ledFisico = est.led || (alertaLocal && ledPisca);
  if (ledFisico != ledAplicado) { digitalWrite(PIN_LED, ledFisico ? HIGH : LOW); ledAplicado = ledFisico; }
  if (est.buzzer != buzzerAplicado) {
    if (est.buzzer) tone(PIN_BUZZER, 1000); else { noTone(PIN_BUZZER); digitalWrite(PIN_BUZZER, LOW); }
    buzzerAplicado = est.buzzer;
  }
}

// ------------------------------------------------------------ MQTT
void aoReceber(char* topico, byte* payload, unsigned int tam) {
  if (strcmp(topico, T_COMANDO) != 0) return;
  Serial.printf("[cmd] %.*s\n", (int)tam, (const char*)payload);
  uint32_t agora = millis();
  g5::Resposta r = est.comando((const char*)payload, tam, agora);
  if (!r.aceito) {
    Serial.printf("[cmd] rejeitado: %s\n", r.motivo);
    publicarConfirmacao(r.commandId, "recebido", "rejeitado", r.motivo);
    return;
  }
  publicarConfirmacao(r.commandId, "recebido", "ok", nullptr);
  aplicarSaidas();
  if (r.cfgMudou) {
    prefs.putInt("limiar", est.cfg.limiarSoloSecoPct);
    prefs.putInt("intervalo", est.cfg.intervaloMinS);
    prefs.putInt("durmax", est.cfg.duracaoMaxS);
  }
  publicarConfirmacao(r.commandId, "executado", "ok", nullptr);
  publicarEstado();  // o backend promove executado -> confirmado ao ver este estado
}

void tentarMqtt(uint32_t agora) {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;
  if (!g5::passou(agora, ultTentativaMqtt, RECONEXAO_MQTT_MS) && ultTentativaMqtt != 0) return;
  ultTentativaMqtt = agora ? agora : 1;
  char offline[80];
  g5::jsonStatus(offline, sizeof offline, DEVICE_ID, false);
  Serial.printf("[mqtt] conectando em %s:%d ...\n", MQTT_HOST, MQTT_PORT);
  if (!mqtt.connect(clientId, nullptr, nullptr, T_STATUS, 1, true, offline)) {  // LWT QoS1 retain
    Serial.printf("[mqtt] falhou rc=%d\n", mqtt.state());
    return;
  }
  Serial.println("[mqtt] conectado");
  mqtt.subscribe(T_COMANDO, 1);
  char online[80];
  g5::jsonStatus(online, sizeof online, DEVICE_ID, true);
  pub(T_STATUS, online, true);
  publicarEstado();
}

// ------------------------------------------------------------ sensores
int lerAdc(int pino) {
  long soma = 0;
  for (int i = 0; i < 8; i++) soma += analogRead(pino);
  return (int)(soma / 8);
}

void lerEPublicar() {
  float t = falhaForcada ? NAN : dht.readTemperature();
  float u = falhaForcada ? NAN : dht.readHumidity();
  bool dhtOk = !isnan(t) && !isnan(u);
  int rawSolo = lerAdc(PIN_SOLO);
  bool soloValido = rawSolo >= SOLO_ADC_MIN_VALIDO && rawSolo <= SOLO_ADC_MAX_VALIDO;
  float solo = soloValido ? g5::pctSolo(rawSolo, SOLO_ADC_SECO, SOLO_ADC_MOLHADO) : NAN;
  float luz = g5::pctLuz(lerAdc(PIN_LDR), LDR_INVERTIDO);
  ultSolo = solo; ultLuz = luz; ultTemp = t;

  bool mudouModo = est.registrarLeituraSensores(dhtOk, soloValido);
  const char* condAntes = condAtual;
  condAtual = g5::condicao(solo, t, luz, est.cfg.limiarSoloSecoPct);
  bool mudouCond = strcmp(condAntes, condAtual) != 0;
  Serial.printf("[sensores] T=%.1f UR=%.1f solo=%.1f%% (raw %d%s) luz=%.1f%% cond=%s modo=%s\n", t, u, solo, rawSolo, soloValido ? "" : " INVALIDO", luz, condAtual, g5::nomeModo(est.modo));

  // valor invalido vai como null: o backend registra falha_sensor e nao grava a leitura
  publicarTelemetria("temperatura", t, "°C");
  publicarTelemetria("umidade_ar", u, "%");
  publicarTelemetria("umidade_solo", solo, "%");
  publicarTelemetria("luminosidade", luz, "%");
  if (!soloValido && est.exigirSolo && est.temBomba) Serial.println("[solo] sem leitura valida: bomba bloqueada (confira o fio AO no GPIO 35)");
  bool irrigou = false;
#if IRRIGACAO_LOCAL
  irrigou = est.irrigarSeSeco(solo, millis());
  if (irrigou) Serial.printf("[irrigacao] solo %.1f%% < limiar %d%%: bomba ligada pela regra local\n", solo, est.cfg.limiarSoloSecoPct);
#endif
  if (mudouModo || mudouCond || irrigou) { aplicarSaidas(); publicarEstado(); }
}

void regraLocal(uint32_t agora) {
  bool deve = !mqtt.connected() && (strcmp(condAtual, "inadequado") == 0 || est.modo == g5::SEGURO);
  if (deve != alertaLocal) {
    alertaLocal = deve;
    ledPisca = false;
    Serial.printf("[regra local] alerta %s\n", deve ? "LIGADO (sem broker)" : "desligado");
  }
  if (alertaLocal && g5::passou(agora, ultPisca, PISCA_ALERTA_MS)) { ultPisca = agora; ledPisca = !ledPisca; }
}

// ------------------------------------------------------------ teste pela serial (Wokwi)
void lerSerial() {
  static String linha;
  while (Serial.available()) {
    char c = Serial.read();
    if (c != '\n' && c != '\r') { linha += c; continue; }
    linha.trim();
    if (linha == "falha") { falhaForcada = !falhaForcada; Serial.printf("[teste] falha do DHT %s\n", falhaForcada ? "FORCADA" : "removida"); }
    else if (linha == "estado") { Serial.printf("[estado] modo=%s bomba=%d led=%d buzzer=%d limiar=%d intervalo=%d durmax=%d\n", g5::nomeModo(est.modo), est.bomba, est.led, est.buzzer, est.cfg.limiarSoloSecoPct, est.cfg.intervaloMinS, est.cfg.duracaoMaxS); }
    else if (linha.length()) Serial.println("[teste] comandos: falha | estado");
    linha = "";
  }
}

// ------------------------------------------------------------ setup / loop
void setup() {
  // rele ANTES de tudo: nivel de repouso (desligado) para a bomba nunca ligar no boot
  releInit();
  pinMode(PIN_LED, OUTPUT); digitalWrite(PIN_LED, LOW);
  pinMode(PIN_BUZZER, OUTPUT); digitalWrite(PIN_BUZZER, LOW);
  Serial.begin(115200);
  analogReadResolution(12);
  dht.begin();
  est.exigirSolo = EXIGIR_SOLO_VALIDO;
  est.temBomba = TEM_BOMBA;

  prefs.begin("g5", false);
  est.cfg.limiarSoloSecoPct = prefs.getInt("limiar", est.cfg.limiarSoloSecoPct);
  est.cfg.intervaloMinS = prefs.getInt("intervalo", est.cfg.intervaloMinS);
  est.cfg.duracaoMaxS = prefs.getInt("durmax", est.cfg.duracaoMaxS);

  snprintf(T_STATUS, sizeof T_STATUS, "%s/%s/status", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_ESTADO, sizeof T_ESTADO, "%s/%s/estado", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_COMANDO, sizeof T_COMANDO, "%s/%s/comando", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_CONF, sizeof T_CONF, "%s/%s/confirmacao", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_TELE, sizeof T_TELE, "%s/%s/telemetria", MQTT_PREFIX, DEVICE_ID);
  snprintf(clientId, sizeof clientId, "%s-%04X", DEVICE_ID, (uint16_t)(ESP.getEfuseMac() >> 32));

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(768);  // estado com "recursos" passa de 300 bytes
  mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  mqtt.setSocketTimeout(3);
  mqtt.setCallback(aoReceber);
  Serial.printf("Estacao %s fw %s | %s | prefixo %s | broker %s:%d\n", DEVICE_ID, FW_VERSION,
                est.temBomba ? "com bomba" : "SEM bomba (alerta por LED/buzzer)", MQTT_PREFIX, MQTT_HOST, MQTT_PORT);
}

void loop() {
  uint32_t agora = millis();

  if (WiFi.status() == WL_CONNECTED && !ntpIniciado) {
    #if USAR_NTP
      configTime(FUSO_SEGUNDOS, 0, "pool.ntp.org", "time.nist.gov");
    #endif
    #if USAR_OTA
      ArduinoOTA.setHostname(DEVICE_ID);
      #ifdef OTA_PASSWORD
        ArduinoOTA.setPassword(OTA_PASSWORD);  // sem senha qualquer um na rede grava firmware
      #endif
      ArduinoOTA.onStart([]() { est.bomba = false; aplicarSaidas(); });  // bomba desligada durante a atualizacao
      ArduinoOTA.begin();
    #endif
    Serial.printf("[wifi] conectado, IP %s\n", WiFi.localIP().toString().c_str());
    ntpIniciado = true;
  }
  #if USAR_OTA
    if (ntpIniciado) ArduinoOTA.handle();
  #endif

  // tempo da bomba ANTES de tentarMqtt: com o broker fora, mqtt.connect() trava ~3 s e a bomba
  // passaria desse tempo do limite de duracao
  if (est.tick(agora)) { aplicarSaidas(); publicarEstado(); }
  tentarMqtt(agora);
  if (mqtt.connected()) mqtt.loop();
  agora = millis();  // o mqtt.loop() pode ter aceitado um comando: nao usar a hora lida antes dele

  if (est.tick(agora)) { aplicarSaidas(); publicarEstado(); }
  regraLocal(agora);
  aplicarSaidas();

  if (primeiraLeitura || g5::passou(agora, ultLeitura, PERIODO_TELEMETRIA_MS)) {
    primeiraLeitura = false;
    ultLeitura = agora;
    lerEPublicar();
  }
  if (g5::passou(agora, ultEstado, PERIODO_ESTADO_MS)) { ultEstado = agora; publicarEstado(); }
  lerSerial();
}
