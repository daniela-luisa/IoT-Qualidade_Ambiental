#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>
#include <Preferences.h>
#include <time.h>
#include <ArduinoJson.h>

#define MODO_SIMULADOR 0

#define DEVICE_ID   "estacao-01"
#define FW_VERSION  "1.6.0"
#define TEM_BOMBA   1

#define MQTT_PREFIX "grupo5"
#define MQTT_PORT   1883

#if MODO_SIMULADOR
  #define WIFI_SSID         "Wokwi-GUEST"
  #define WIFI_PASSWORD     ""
  #define MQTT_HOST         "host.wokwi.internal"
  #define DHT_TIPO          DHT22
  #define RELE_ATIVO_BAIXO  0
  #define MQTT_KEEPALIVE_S  60
  #define USAR_OTA          0
  #define USAR_NTP          0
#else
  #if __has_include("secrets.h")
    #include "secrets.h"
  #endif
  #ifndef WIFI_SSID
    #define WIFI_SSID       "TDESI"
    #define WIFI_PASSWORD   "leo12345"
  #endif
  #ifndef MQTT_HOST
    #define MQTT_HOST       "10.252.1.58"
  #endif
  #define DHT_TIPO          DHT11
  #define RELE_ATIVO_BAIXO  1
  #define MQTT_KEEPALIVE_S  15
  #define USAR_OTA          1
  #define USAR_NTP          1
#endif
#define LDR_INVERTIDO     1

#define PIN_DHT     14
#define PIN_SOLO    35
#define PIN_LDR     34
#define PIN_LED     26
#define PIN_BUZZER  25
#define PIN_RELE    27
#define PIN_BOTAO   0

#define SOLO_ADC_SECO     3200
#define SOLO_ADC_MOLHADO  1200
#define SOLO_ADC_MIN_VALIDO  100
#define SOLO_ADC_MAX_VALIDO  4095
#define EXIGIR_SOLO_VALIDO   1
#define IRRIGACAO_LOCAL      1

#define PERIODO_TELEMETRIA_MS  5000UL
#define PERIODO_ESTADO_MS      30000UL
#define RECONEXAO_MQTT_MS      5000UL
#define AVISO_BOMBA_MS         1000UL
#define FUSO_SEGUNDOS          (-3 * 3600)
#define HISTORICO_OFFLINE      USAR_NTP
#define HIST_MAX_LEITURAS      1440
#define HIST_MAX_EVENTOS       64
#define HIST_ENVIO_MS          50UL
#if USAR_OTA
  #include <ArduinoOTA.h>
#endif

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <time.h>

namespace g5 {

enum Modo : uint8_t { AUTOMATICO = 0, MANUAL = 1, SEGURO = 2 };
inline const char* nomeModo(Modo m) { return m == AUTOMATICO ? "automatico" : (m == MANUAL ? "manual" : "seguro"); }

constexpr int BOMBA_DUR_MIN = 1, BOMBA_DUR_MAX = 15, BOMBA_DUR_PADRAO = 3;
constexpr int LED_DUR_MIN = 1, LED_DUR_MAX = 60, LED_DUR_PADRAO = 8;
constexpr uint32_t MANUAL_TIMEOUT_MS = 60000UL;
constexpr int FALHAS_PARA_SEGURO = 3;
constexpr int N_VISTOS = 16;

inline bool passou(uint32_t agora, uint32_t ref, uint32_t ms) { int32_t d = (int32_t)(agora - ref); return d >= 0 && (uint32_t)d >= ms; }

struct Cfg {
  int limiarSoloSecoPct = 30;
  int intervaloMinS = 60;
  int duracaoMaxS = 15;
  int bombaBloqueada = 0;
};

struct Resposta {
  char commandId[40] = "?";
  bool aceito = false;
  const char* motivo = nullptr;
  bool estadoMudou = false;
  bool cfgMudou = false;
};

class Estacao {
 public:
  Cfg cfg;
  Modo modo = SEGURO;
  bool bomba = false, led = false, buzzer = false;
  bool temBomba = true;
  bool exigirSolo = true;
  bool soloOk = false;
  uint32_t avisoBombaMs = 0;

  bool bombaAcionada(uint32_t agora) const { return bomba && passou(agora, bombaIni, 0); }
  int32_t msNoAviso(uint32_t agora) const {
    if (!bomba || passou(agora, bombaIni, 0)) return -1;
    return (int32_t)avisoBombaMs - (int32_t)(bombaIni - agora);
  }
  bool silenciar() {
    bool mudou = led || buzzer;
    led = false; buzzer = false;
    return mudou;
  }

  bool tick(uint32_t agora) {
    bool mudou = false;
    if (bomba) {
      int lim = durBombaS < cfg.duracaoMaxS ? durBombaS : cfg.duracaoMaxS;
      if (passou(agora, bombaIni, (uint32_t)lim * 1000UL)) { bomba = false; mudou = true; }
    }
    if (led && passou(agora, ledIni, (uint32_t)durLedS * 1000UL)) { led = false; mudou = true; }
    if (buzzer && passou(agora, buzzerIni, (uint32_t)durBuzzerS * 1000UL)) { buzzer = false; mudou = true; }
    if (modo == MANUAL && passou(agora, manualDesde, MANUAL_TIMEOUT_MS)) { modo = AUTOMATICO; mudou = true; }
    return mudou;
  }

  bool registrarLeituraSensores(bool dhtOk, bool soloValido) {
    soloOk = soloValido;
    bool mudou = false;
    if (exigirSolo && !soloValido && bomba) { bomba = false; mudou = true; }
    bool ok = dhtOk && (soloValido || !exigirSolo);
    if (ok) {
      falhasSeguidas = 0;
      if (modo == SEGURO) { modo = AUTOMATICO; return true; }
      return mudou;
    }
    if (falhasSeguidas < 1000) falhasSeguidas++;
    if (falhasSeguidas >= FALHAS_PARA_SEGURO && modo != SEGURO) {
      modo = SEGURO;
      bomba = false;
      return true;
    }
    return mudou;
  }

  bool irrigarSeSeco(float soloPct, uint32_t agora) {
    if (!temBomba || modo != AUTOMATICO || bomba || cfg.bombaBloqueada) return false;
    if (exigirSolo && !soloOk) return false;
    if (isnan(soloPct) || soloPct >= (float)cfg.limiarSoloSecoPct) return false;
    if (jaIrrigou && !passou(agora, bombaIni, (uint32_t)cfg.intervaloMinS * 1000UL)) return false;
    int dur = BOMBA_DUR_PADRAO < cfg.duracaoMaxS ? BOMBA_DUR_PADRAO : cfg.duracaoMaxS;
    bomba = true; bombaIni = agora + avisoBombaMs; durBombaS = dur; jaIrrigou = true;
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
    if (!temBomba && strcmp(alvo, "bomba") == 0) return rejeitar(r, "alvo_invalido");
    if (modo == SEGURO && ehAtuador && strcmp(acao, "off") != 0) return rejeitar(r, "modo_seguro");
    if (exigirSolo && !soloOk && strcmp(alvo, "bomba") == 0 && strcmp(acao, "on") == 0) return rejeitar(r, "modo_seguro");
    if (cfg.bombaBloqueada && strcmp(alvo, "bomba") == 0 && strcmp(acao, "on") == 0) return rejeitar(r, "bomba_bloqueada");

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
      if (ligar) { bomba = true; bombaIni = agora + avisoBombaMs; durBombaS = dur; jaIrrigou = true; } else bomba = false;
    } else if (strcmp(alvo, "led") == 0) {
      if (ligar) { led = true; ledIni = agora; durLedS = dur; } else led = false;
    } else {
      if (ligar) { buzzer = true; buzzerIni = agora; durBuzzerS = dur; } else buzzer = false;
    }
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
    constexpr int N = 4;
    Item itens[N] = {{"limiarSoloSecoPct", 10, 80, &cfg.limiarSoloSecoPct},
                     {"intervaloMinS", 30, 3600, &cfg.intervaloMinS},
                     {"duracaoMaxS", 1, 15, &cfg.duracaoMaxS},
                     {"bombaBloqueada", 0, 1, &cfg.bombaBloqueada}};
    int novos[N];
    int presentes = 0;
    for (int i = 0; i < N; i++) {
      JsonVariantConst v = doc[itens[i].nome];
      novos[i] = *itens[i].destino;
      if (v.isNull()) continue;
      if (!v.is<int>() || v.as<int>() < itens[i].minimo || v.as<int>() > itens[i].maximo) return rejeitar(r, "valor_fora_da_faixa");
      novos[i] = v.as<int>();
      presentes++;
    }
    if (presentes == 0) return rejeitar(r, "valor_fora_da_faixa");
    for (int i = 0; i < N; i++) *itens[i].destino = novos[i];
    if (cfg.bombaBloqueada && bomba) bomba = false;
    r.aceito = true;
    r.cfgMudou = true;
    r.estadoMudou = true;
    return r;
  }
};

inline float limitar(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

inline float pctSolo(int raw, int secoRaw, int molhadoRaw) {
  if (secoRaw == molhadoRaw) return 0;
  return limitar((float)(secoRaw - raw) * 100.0f / (float)(secoRaw - molhadoRaw), 0, 100);
}
inline float pctLuz(int raw, bool invertido) {
  float p = limitar((float)raw * 100.0f / 4095.0f, 0, 100);
  return invertido ? 100.0f - p : p;
}
inline const char* condicao(float soloPct, float temp, float luzPct, int limiarSolo) {
  bool tempRuim = !isnan(temp) && (temp > 32.0f || temp < 10.0f);
  if (isnan(soloPct) || soloPct < (float)limiarSolo || tempRuim) return "inadequado";
  if (luzPct < 20.0f) return "atencao";
  return "adequado";
}

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
  if (e.temBomba) d["bomba"] = e.bomba ? "on" : "off";
  d["led"] = e.led ? "on" : "off"; d["buzzer"] = e.buzzer ? "on" : "off";
  d["fw"] = fw;
  JsonObject rec = d["recursos"].to<JsonObject>();
  JsonArray sens = rec["sensores"].to<JsonArray>();
  sens.add("temperatura"); sens.add("umidade_ar"); sens.add("umidade_solo"); sens.add("luminosidade");
  JsonArray atu = rec["atuadores"].to<JsonArray>();
  if (e.temBomba) atu.add("bomba");
  atu.add("led"); atu.add("buzzer");
  d["limiarSoloSecoPct"] = e.cfg.limiarSoloSecoPct; d["intervaloMinS"] = e.cfg.intervaloMinS; d["duracaoMaxS"] = e.cfg.duracaoMaxS;
  if (e.temBomba) d["bombaBloqueada"] = e.cfg.bombaBloqueada;
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
inline size_t jsonEvento(char* buf, size_t n, const char* dev, const char* ts, const char* tipo, const char* detalhe) {
  JsonDocument d;
  d["v"] = 1; d["deviceId"] = dev;
  if (ts[0]) d["ts"] = ts; else d["ts"] = nullptr;
  d["tipo"] = tipo; d["detalhe"] = detalhe;
  return serializeJson(d, buf, n);
}

inline void formatarTs(char* buf, size_t n, const struct tm& t) {
  char z[8] = "";
  strftime(buf, n, "%Y-%m-%dT%H:%M:%S", &t);
  strftime(z, sizeof(z), "%z", &t);
  size_t l = strlen(buf);
  if (strlen(z) == 5 && l + 7 <= n) snprintf(buf + l, n - l, "%.3s:%.2s", z, z + 3);
}

}

DHT dht(PIN_DHT, DHT_TIPO);
WiFiClient wifi;
PubSubClient mqtt(wifi);
Preferences prefs;
g5::Estacao est;

char T_STATUS[80], T_ESTADO[80], T_COMANDO[80], T_CONF[80], T_TELE[80], T_EVENTO[80];
char clientId[48];
const char* condAtual = "indefinida";
uint32_t ultLeitura = 0, ultEstado = 0, ultTentativaMqtt = 0;
bool primeiraLeitura = true, ntpIniciado = false, falhaForcada = false;
bool ledAplicado = false, buzzerAplicado = false, releAplicado = false;
float ultSolo = NAN, ultLuz = NAN, ultTemp = NAN;
bool alertaLocal = false;

#if HISTORICO_OFFLINE
struct Amostra { uint32_t ms; float t, u, solo, luz; };
struct EventoOff { uint32_t ms; char tipo[10]; char detalhe[48]; };
Amostra histLeituras[HIST_MAX_LEITURAS];
EventoOff histEventos[HIST_MAX_EVENTOS];
int hlIni = 0, hlQtd = 0, heIni = 0, heQtd = 0;
uint32_t ultEnvioHist = 0;
#endif

void agoraTs(char* buf, size_t n) {
  buf[0] = 0;
  time_t t = time(nullptr);
  if (t < 1700000000) return;
  struct tm info;
  localtime_r(&t, &info);
  g5::formatarTs(buf, n, info);
}

void pub(const char* topico, const char* payload, bool retain) {
  if (!mqtt.connected()) return;
  mqtt.publish(topico, payload, retain);
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

void guardarLeitura(float t, float u, float solo, float luz, uint32_t agora) {
#if HISTORICO_OFFLINE
  int pos;
  bool cheio = hlQtd == HIST_MAX_LEITURAS;
  if (cheio) {
    pos = hlIni;
    hlIni = (hlIni + 1) % HIST_MAX_LEITURAS;
  } else {
    pos = (hlIni + hlQtd) % HIST_MAX_LEITURAS;
    hlQtd++;
  }
  histLeituras[pos] = {agora, t, u, solo, luz};
  if (!cheio && (hlQtd == 1 || hlQtd % 60 == 0))
    Serial.printf("[historico] sem broker: %d leitura(s) e %d evento(s) guardados\n", hlQtd, heQtd);
  else if (cheio && hlIni == 1)
    Serial.println("[historico] memoria cheia: descartando as leituras mais antigas");
#endif
}

void guardarEvento(const char* tipo, const char* detalhe) {
#if HISTORICO_OFFLINE
  int pos;
  if (heQtd == HIST_MAX_EVENTOS) {
    pos = heIni;
    heIni = (heIni + 1) % HIST_MAX_EVENTOS;
  } else {
    pos = (heIni + heQtd) % HIST_MAX_EVENTOS;
    heQtd++;
  }
  EventoOff& e = histEventos[pos];
  e.ms = millis();
  strncpy(e.tipo, tipo, sizeof e.tipo - 1); e.tipo[sizeof e.tipo - 1] = 0;
  strncpy(e.detalhe, detalhe, sizeof e.detalhe - 1); e.detalhe[sizeof e.detalhe - 1] = 0;
  Serial.printf("[historico] evento guardado: %s %s\n", tipo, detalhe);
#endif
}

void anotarMudancas() {
  static bool bombaAnt = false;
  static g5::Modo modoAnt = g5::SEGURO;
  static const char* condAnt = "indefinida";
  bool semBroker = !mqtt.connected();
  char det[48];
  if (est.bomba != bombaAnt) {
    bombaAnt = est.bomba;
    if (semBroker) {
      snprintf(det, sizeof det, "%s (modo %s, offline)", est.bomba ? "on" : "off", g5::nomeModo(est.modo));
      guardarEvento("bomba", det);
    }
  }
  if (est.modo != modoAnt) {
    modoAnt = est.modo;
    if (semBroker) { snprintf(det, sizeof det, "%s (offline)", g5::nomeModo(est.modo)); guardarEvento("modo", det); }
  }
  if (strcmp(condAtual, condAnt) != 0) {
    bool primeira = strcmp(condAnt, "indefinida") == 0;
    condAnt = condAtual;
    if (semBroker && !primeira) { snprintf(det, sizeof det, "%s (offline)", condAtual); guardarEvento("condicao", det); }
  }
}

void enviarHistorico(uint32_t agora) {
#if HISTORICO_OFFLINE
  if (!mqtt.connected() || (hlQtd == 0 && heQtd == 0)) return;
  time_t agoraEpoch = time(nullptr);
  if (agoraEpoch < 1700000000) return;
  if (!g5::passou(agora, ultEnvioHist, HIST_ENVIO_MS)) return;
  ultEnvioHist = agora;

  char ts[32], buf[250], topico[96];
  struct tm info;
  if (heQtd > 0) {
    EventoOff& e = histEventos[heIni];
    time_t quando = agoraEpoch - (time_t)((agora - e.ms) / 1000UL);
    localtime_r(&quando, &info);
    g5::formatarTs(ts, sizeof ts, info);
    g5::jsonEvento(buf, sizeof buf, DEVICE_ID, ts, e.tipo, e.detalhe);
    if (!mqtt.publish(T_EVENTO, buf, false)) return;
    heIni = (heIni + 1) % HIST_MAX_EVENTOS;
    heQtd--;
  } else {
    Amostra& a = histLeituras[hlIni];
    time_t quando = agoraEpoch - (time_t)((agora - a.ms) / 1000UL);
    localtime_r(&quando, &info);
    g5::formatarTs(ts, sizeof ts, info);
    struct { const char* g; float v; const char* u; } itens[4] = {
      {"temperatura", a.t, "°C"}, {"umidade_ar", a.u, "%"}, {"umidade_solo", a.solo, "%"}, {"luminosidade", a.luz, "%"}};
    for (auto& it : itens) {
      if (isnan(it.v)) continue;
      g5::jsonTelemetria(buf, sizeof buf, DEVICE_ID, ts, it.g, it.v, it.u);
      snprintf(topico, sizeof topico, "%s/%s", T_TELE, it.g);
      if (!mqtt.publish(topico, buf, false)) return;
    }
    hlIni = (hlIni + 1) % HIST_MAX_LEITURAS;
    hlQtd--;
  }
  if (hlQtd == 0 && heQtd == 0) Serial.println("[historico] reenvio concluido: backend com o historico completo");
#endif
}

void releInit() {
#if RELE_ATIVO_BAIXO
  pinMode(PIN_RELE, INPUT);
#else
  digitalWrite(PIN_RELE, LOW);
  pinMode(PIN_RELE, OUTPUT);
  digitalWrite(PIN_RELE, LOW);
#endif
}

void releFisico(bool ligar) {
#if RELE_ATIVO_BAIXO
  if (ligar) { digitalWrite(PIN_RELE, LOW); pinMode(PIN_RELE, OUTPUT); digitalWrite(PIN_RELE, LOW); }
  else       { pinMode(PIN_RELE, INPUT); }
#else
  digitalWrite(PIN_RELE, ligar ? HIGH : LOW);
#endif
}

bool padraoLed(uint32_t agora) {
  if (est.led) return true;
  bool problema = est.modo == g5::SEGURO || !mqtt.connected();
  if (alertaLocal && est.modo != g5::SEGURO) return true;
  if (problema) return (agora / 500) % 2;
  return false;
}

bool padraoBuzzer(uint32_t agora) {
  if (est.buzzer) return true;
  int32_t t = est.msNoAviso(agora);
  return t >= 0 && (t < 150 || (t >= 300 && t < 450));
}

void aplicarSaidas() {
  uint32_t agora = millis();
  bool releLigar = est.bombaAcionada(agora);
  if (releLigar != releAplicado) {
    releFisico(releLigar);
    releAplicado = releLigar;
    Serial.printf("[bomba] %s\n", releLigar ? "LIGADA" : "desligada");
  }
  bool ledFisico = padraoLed(agora);
  if (ledFisico != ledAplicado) { digitalWrite(PIN_LED, ledFisico ? HIGH : LOW); ledAplicado = ledFisico; }
  bool buzzerFisico = padraoBuzzer(agora);
  if (buzzerFisico != buzzerAplicado) {
    if (buzzerFisico) tone(PIN_BUZZER, 1000); else { noTone(PIN_BUZZER); digitalWrite(PIN_BUZZER, LOW); }
    buzzerAplicado = buzzerFisico;
  }
}

void publicarEvento(const char* tipo, const char* detalhe) {
  if (!mqtt.connected()) { guardarEvento(tipo, detalhe); return; }
  char ts[32], buf[250];
  agoraTs(ts, sizeof ts);
  g5::jsonEvento(buf, sizeof buf, DEVICE_ID, ts, tipo, detalhe);
  pub(T_EVENTO, buf, false);
}

void lerBotao(uint32_t agora) {
  static bool apertadoAnt = false;
  static uint32_t ultMudanca = 0;
  bool apertado = digitalRead(PIN_BOTAO) == LOW;
  if (apertado == apertadoAnt || !g5::passou(agora, ultMudanca, 50)) return;
  apertadoAnt = apertado;
  ultMudanca = agora;
  if (!apertado) return;
  bool apagou = est.silenciar();
  Serial.printf("[botao] alarme silenciado%s\n", apagou ? " (LED/buzzer desligados)" : "");
  aplicarSaidas();
  publicarEvento("alarme", "silenciado no botao da placa");
  if (apagou) publicarEstado();
}

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
    prefs.putInt("bloq", est.cfg.bombaBloqueada);
    if (est.cfg.bombaBloqueada) Serial.println("[cmd] bomba BLOQUEADA pelo backend (reservatorio vazio?)");
  }
  publicarConfirmacao(r.commandId, "executado", "ok", nullptr);
  publicarEstado();
}

void tentarMqtt(uint32_t agora) {
  if (mqtt.connected() || WiFi.status() != WL_CONNECTED) return;
  if (!g5::passou(agora, ultTentativaMqtt, RECONEXAO_MQTT_MS) && ultTentativaMqtt != 0) return;
  ultTentativaMqtt = agora ? agora : 1;
  char offline[80];
  g5::jsonStatus(offline, sizeof offline, DEVICE_ID, false);
  Serial.printf("[mqtt] conectando em %s:%d ...\n", MQTT_HOST, MQTT_PORT);
  if (!mqtt.connect(clientId, nullptr, nullptr, T_STATUS, 1, true, offline)) {
    Serial.printf("[mqtt] falhou rc=%d\n", mqtt.state());
    return;
  }
  Serial.println("[mqtt] conectado");
  mqtt.subscribe(T_COMANDO, 1);
  char online[80];
  g5::jsonStatus(online, sizeof online, DEVICE_ID, true);
  pub(T_STATUS, online, true);
  publicarEstado();
#if HISTORICO_OFFLINE
  if (hlQtd || heQtd) Serial.printf("[historico] reenviando %d leitura(s) e %d evento(s) guardados offline\n", hlQtd, heQtd);
#endif
}

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

  if (!mqtt.connected()) guardarLeitura(t, u, solo, luz, millis());
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

void regraLocal() {
  bool deve = !mqtt.connected() && (strcmp(condAtual, "inadequado") == 0 || est.modo == g5::SEGURO);
  if (deve != alertaLocal) {
    alertaLocal = deve;
    Serial.printf("[regra local] alerta %s\n", deve ? "LIGADO (sem broker): LED aceso" : "desligado");
  }
}

void lerSerial() {
  static String linha;
  while (Serial.available()) {
    char c = Serial.read();
    if (c != '\n' && c != '\r') { linha += c; continue; }
    linha.trim();
    if (linha == "falha") { falhaForcada = !falhaForcada; Serial.printf("[teste] falha do DHT %s\n", falhaForcada ? "FORCADA" : "removida"); }
    else if (linha == "estado") { Serial.printf("[estado] modo=%s bomba=%d led=%d buzzer=%d limiar=%d intervalo=%d durmax=%d bloqueada=%d\n", g5::nomeModo(est.modo), est.bomba, est.led, est.buzzer, est.cfg.limiarSoloSecoPct, est.cfg.intervaloMinS, est.cfg.duracaoMaxS, est.cfg.bombaBloqueada); }
    else if (linha.length()) Serial.println("[teste] comandos: falha | estado");
    linha = "";
  }
}

void setup() {
  releInit();
  pinMode(PIN_LED, OUTPUT); digitalWrite(PIN_LED, LOW);
  pinMode(PIN_BOTAO, INPUT_PULLUP);
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
  est.cfg.bombaBloqueada = prefs.getInt("bloq", 0);
  est.avisoBombaMs = AVISO_BOMBA_MS;

  snprintf(T_STATUS, sizeof T_STATUS, "%s/%s/status", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_ESTADO, sizeof T_ESTADO, "%s/%s/estado", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_COMANDO, sizeof T_COMANDO, "%s/%s/comando", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_CONF, sizeof T_CONF, "%s/%s/confirmacao", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_TELE, sizeof T_TELE, "%s/%s/telemetria", MQTT_PREFIX, DEVICE_ID);
  snprintf(T_EVENTO, sizeof T_EVENTO, "%s/%s/evento", MQTT_PREFIX, DEVICE_ID);
  snprintf(clientId, sizeof clientId, "%s-%04X", DEVICE_ID, (uint16_t)(ESP.getEfuseMac() >> 32));

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(768);
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
        ArduinoOTA.setPassword(OTA_PASSWORD);
      #endif
      ArduinoOTA.onStart([]() { est.bomba = false; aplicarSaidas(); });
      ArduinoOTA.begin();
    #endif
    Serial.printf("[wifi] conectado, IP %s\n", WiFi.localIP().toString().c_str());
    ntpIniciado = true;
  }
  #if USAR_OTA
    if (ntpIniciado) ArduinoOTA.handle();
  #endif

  if (est.tick(agora)) { aplicarSaidas(); publicarEstado(); }
  tentarMqtt(agora);
  if (mqtt.connected()) mqtt.loop();
  agora = millis();

  if (est.tick(agora)) { aplicarSaidas(); publicarEstado(); }
  regraLocal();
  aplicarSaidas();

  if (primeiraLeitura || g5::passou(agora, ultLeitura, PERIODO_TELEMETRIA_MS)) {
    primeiraLeitura = false;
    ultLeitura = agora;
    lerEPublicar();
  }
  if (g5::passou(agora, ultEstado, PERIODO_ESTADO_MS)) { ultEstado = agora; publicarEstado(); }
  lerBotao(agora);
  anotarMudancas();
  enviarHistorico(agora);
  lerSerial();
}
