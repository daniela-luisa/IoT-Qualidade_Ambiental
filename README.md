# Projeto IoT - Grupo 5 - Monitoramento Ambiental para Crescimento de Plantas

## Integrantes

- Ana Beatriz Pedrozo
- Daniela Luisa da Conceição
- Marcelo Gustavo Eger
- João Henrique Souza Rocha
- Leonardo André Ferreira
- Willian Squena

## Família temática

Grupo 5 - Qualidade Ambiental.

Construção de uma pequena estação ambiental conectada, que mede variáveis do ambiente relevantes para o crescimento de plantas, classifica a condição do local e sinaliza a condição por LED e buzzer.

## Problema

O crescimento saudável de uma planta depende de condições ambientais específicas — temperatura, umidade do ar, umidade do solo e luminosidade — que costumam variar ao longo do dia sem que o responsável pela planta perceba a tempo. Isso é ainda mais crítico em ambientes internos, estufas pequenas ou hortas domésticas, onde não há monitoramento automático: a pessoa só percebe que algo está errado (solo seco, pouca luz, calor excessivo) quando a planta já apresenta sinais visíveis de estresse, como murchamento ou folhas amareladas — quando muitas vezes já é tarde para reverter o dano.

## Usuário ou contexto de uso

Pessoas que cultivam plantas em casa, hortas domésticas ou pequenas estufas, e que não têm conhecimento técnico de agronomia nem tempo para checar manualmente as condições da planta todos os dias.

## Objetivo do Projeto

Construir um dispositivo com ESP32 que:

- leia pelo menos duas variáveis ambientais relevantes para plantas (temperatura, umidade do ar, umidade do solo e/ou luminosidade);
- classifique a condição do ambiente para o crescimento da planta;
- publique telemetria via MQTT;
- receba comando remoto;
- acione um alerta físico (LED e buzzer) e confirme a ação executada.

## Como funciona hoje (N3)

O hardware mede, a rede transporta, o backend valida, decide e guarda, a interface informa e o usuário comanda — e todo comando volta com confirmação do dispositivo.

```
ESP32 (estacao-01/02) ──MQTT──> Mosquitto ──> Backend FastAPI ──> PostgreSQL
      ^  telemetria, estado,                    │  valida contrato v1, grava,
      │  confirmação, LWT                       │  regra de irrigação, alertas, e-mail
      └────────── comando (commandId) <─────────┘<── Interface web (login, comando, painel)
```

### O que cada parte faz

| Parte | Responsabilidade |
|---|---|
| **Firmware** (`firmware/…/grupo5_estacao_ambiental.ino`) | Lê DHT11 (temperatura/umidade do ar), solo e LDR (ADC, em %), classifica a condição, publica telemetria a cada 5 s e estado a cada 30 s (e sempre que algo muda). Executa comandos com intertravamento, guarda a configuração na NVS, tem LWT, NTP e OTA. |
| **Backend** (`backend/app`) | Assina os tópicos, valida cada mensagem contra o [contrato v1](CONTRATOS.md), grava leituras e eventos, acompanha o ciclo de cada comando, roda a regra de irrigação remota, abre/resolve alertas e envia e-mail. |
| **Banco** (`db/schema.sql`) | Dispositivos, leituras, comandos (ciclo completo), regras, alertas, eventos, notificações e usuários (só hash bcrypt). |
| **Web** (`web/`) | Login (admin comanda, operador só vê), cartões online/offline com os sensores e atuadores **que cada estação tem** e suas últimas leituras, alertas, envio de comando (ligar/desligar), tabela de comandos com estado, painel analítico com média móvel, edição da regra e histórico de eventos. |

### Comunicação (contrato MQTT v1)

Tópicos `grupo5/{deviceId}/…` com JSON versionado (`"v":1`) — detalhes e exemplos em [`CONTRATOS.md`](CONTRATOS.md).

| Tópico | Quem publica | Quando |
|---|---|---|
| `telemetria/{grandeza}` | dispositivo | a cada 5 s (valor inválido vai `null` → backend registra `falha_sensor`, no máximo 1 por minuto por grandeza) |
| `estado` (retain) | dispositivo | a cada 30 s e em toda mudança (modo, condição, atuador) |
| `status` (retain, LWT) | dispositivo / broker | `online` ao conectar; `offline` publicado pelo broker se a placa cair |
| `comando` | backend | quando um admin envia ou a regra decide |
| `confirmacao` | dispositivo | `recebido` (ok ou `rejeitado` + motivo) → `executado` |

**Ciclo do comando:** `enviado` → `recebido` → `executado` → `confirmado` (o backend promove para confirmado ao ver o `estado` publicado logo depois). Finais alternativos: `rejeitado` (com motivo do catálogo), `falhou` (broker fora) e `expirado` (sem resposta em `COMANDO_TIMEOUT_S`).

### Efeitos físicos (atuadores)

| Atuador | Efeito | Duração | Quem aciona |
|---|---|---|---|
| **Bomba** (relé no GPIO 27) | Irriga o vaso | 1 a 15 s (padrão 5), desliga sozinha; nunca acima de `duracaoMaxS` | Admin, regra remota do backend ou regra local da placa |
| **LED** (GPIO 26) | Alerta visual | 1 a 60 s (padrão 8) | Admin; **pisca sozinho** se a placa ficar sem broker com condição `inadequado` ou em modo seguro |
| **Buzzer** (GPIO 25, `tone` 1 kHz) | Alerta sonoro | 1 a 60 s (padrão 8) | Admin |

### Estação sem bomba

Cada placa declara no `estado` o que tem montado (`"recursos": {"sensores": [...], "atuadores": [...]}`), definido no firmware por `TEM_BOMBA` (1 = com relé/bomba, 0 = sem). A partir disso:

- **Interface:** o cartão mostra só os sensores e atuadores daquela estação (com o selo "sem bomba"), o formulário de comando só oferece os atuadores que ela tem e o painel só mostra os gráficos dos sensores dela.
- **Backend:** recusa comando para atuador que a estação não tem (`422 alvo_invalido`, nem publica) e o firmware também rejeita, se chegar.
- **Solo seco sem bomba:** como não dá para irrigar, a regra **acende o LED** (fica aceso até o próximo aviso, máx. 60 s) e **toca o buzzer** (bipe com a duração da regra), abre o alerta "sem bomba: regar manualmente" e manda e-mail. Repete a cada intervalo mínimo enquanto o solo seguir seco e **apaga LED e buzzer** quando alguém rega e a umidade volta ao limiar.
- Firmware antigo, sem o campo `recursos`, é tratado como "tem tudo".

Toda vez que a bomba liga ou desliga de fato (inclusive pela regra local da placa, que não passa por comando), o backend registra um evento `bomba` e o painel conta em "Bomba ligou".

### Classificação e modos do dispositivo

- **Condição:** `inadequado` se solo abaixo de `limiarSoloSecoPct`, solo sem leitura ou temperatura fora de 10–32 °C; `atencao` se luz abaixo de 20 %; senão `adequado`.
- **Modos:** `automatico` (normal); `manual` por 60 s depois de qualquer comando de pessoa (a irrigação automática — local e remota — pausa); `seguro` no boot e após 3 falhas seguidas de sensor (bomba desligada e só aceita comandos de desligar/config).
- **Segurança da bomba:** só liga com leitura válida do solo; intervalo mínimo (`intervaloMinS`) entre irrigações; teto de duração; desligada durante OTA; relé em estado desligado desde o boot.

### Regra de irrigação remota (backend)

A cada `REGRAS_INTERVALO_S` o backend calcula a média da umidade do solo na janela configurada. Dispara `bomba on` (origem `regra`) — ou LED + buzzer na [estação sem bomba](#estação-sem-bomba) — quando **a média e a leitura mais recente** estão abaixo do limiar, a estação está online, fora dos modos `seguro`/`manual`, a bomba não está ligada e já passou o intervalo mínimo desde a última **tentativa** (comando rejeitado também conta — sem reenvio em loop). Abre o alerta `solo_seco` (e manda um e-mail) e o resolve quando a média volta ao limiar.

## Como rodar

```bash
cp .env.example .env              # troque as senhas e o JWT_SECRET (32+ caracteres; vazio o backend não sobe)
docker compose up -d --build      # Mosquitto :1883, PostgreSQL :5432, backend + web :8000
open http://localhost:8000        # login com ADMIN_EMAIL / ADMIN_PASSWORD do .env
```

**Sem placa (simulador):**

```bash
pip install paho-mqtt
python tests/simulador_dispositivo.py --device estacao-01 --seco               # solo seco: a regra irriga sozinha
python tests/simulador_dispositivo.py --device estacao-02 --seco --sem-bomba   # sem bomba: a regra aciona LED + buzzer
ADMIN_EMAIL=... ADMIN_PASSWORD=... python tests/e2e_smoke.py estacao-01
```

**Placa física:** crie `firmware/grupo5_estacao_ambiental/secrets.h` (fora do git):

```cpp
#define WIFI_SSID "sua_rede"
#define WIFI_PASSWORD "sua_senha"
#define MQTT_HOST "192.168.0.10"   // IP do notebook com o docker compose (macOS: ipconfig getifaddr en0)
#define OTA_PASSWORD "opcional"
```

Ajuste `DEVICE_ID` (`estacao-01` / `estacao-02`) e `TEM_BOMBA` (0 na placa que não tem relé/bomba) e grave. **Wokwi:** `MODO_SIMULADOR 1` (usa `Wokwi-GUEST` e `host.wokwi.internal`; precisa do Private IoT Gateway).

**Testes** (precisa de um PostgreSQL de teste; o CI faz isso sozinho):

```bash
pip install -r backend/requirements-dev.txt
TEST_DATABASE_URL=postgresql+psycopg://macp:senha@localhost:5432/macp_test pytest tests
```

## Sensor e atuador reais

- **Sensor de temperatura e umidade do ar**: DHT11 
- **Sensor de umidade do solo**: módulo capacitivo, saída analógica (AO no GPIO 35, ADC1) convertida em % pela calibração `SOLO_ADC_SECO`/`SOLO_ADC_MOLHADO` do firmware (na N1 era a saída digital do comparador)
- **Sensor de luminosidade**: módulo LDR, saída analógica (AO no GPIO 34, ADC1) em %
- **Atuador de irrigação**: mini bomba 5 V com fonte própria, via módulo relé no GPIO 27 (ativo em nível baixo)
- **Atuador/alerta**: LED (com resistor em série) + Buzzer passivo

## Hardware detalhado

- **Alimentação**: cabo USB conectado ao computador para os testes de bancada; possibilidade de uso de power bank 5V para demonstração fora da tomada.
- **Protoboard**: protoboard padrão de 830 pontos.
- **Estrutura/caixa**: suporte simples (impresso em 3D ou montado em caixa de papelão/acrílico) para fixar o ESP32 e os sensores próximo ao vaso da planta.
- **Conectores**: jumpers macho-macho e macho-fêmea; resistor em série com o LED (conforme orientação de segurança do guia da disciplina).
- **Leitura do solo e da luminosidade**: na N3 os dois módulos são lidos pela saída analógica (AO) no ADC1, com média de 8 amostras, e convertidos em %. A calibração do solo fica no firmware (`SOLO_ADC_SECO` no ar, `SOLO_ADC_MOLHADO` na água); leitura bruta abaixo de `SOLO_ADC_MIN_VALIDO` é tratada como fio solto/sensor sem alimentação e bloqueia a bomba. (Na N1 era a saída digital do comparador, ajustada no potenciômetro do módulo.)
- **Pinagem**: solo no GPIO35 e luminosidade no GPIO34 (ADC1, só entrada, funcionam com Wi-Fi ligado); relé da bomba no GPIO27 — mapa completo em [`docs/mapa_de_pinos.md`](docs/mapa_de_pinos.md).
- **Buzzer**: é passivo, então é acionado com `tone()`/`noTone()` (gerando uma frequência de 1000Hz), não com `digitalWrite()` — esse tipo de buzzer não emite som só com uma tensão fixa.

## Segurança / credenciais

As credenciais de Wi-Fi (`WIFI_SSID`, `WIFI_PASSWORD`), o IP do broker (`MQTT_HOST`) e a senha de OTA ficam em um arquivo `secrets.h` (formato completo em [Como rodar](#como-rodar)), que **não é versionado** (está listado no `.gitignore`). O firmware principal só faz `#include "secrets.h"`; quem for gravar a placa cria esse arquivo manualmente.

## Protótipo do produto

![Protótipo simulado no Wokwi](docs/prototipo_wokwi.jpeg)

Protótipo montado com o ESP32 conectado aos sensores e atuadores via protoboard: DHT11 no GPIO14, sensor de solo e módulo LDR (saída digital) no GPIO35 e GPIO34, buzzer no GPIO25 e LED (com resistor em série) no GPIO26.

**Status de validação:** o firmware foi validado em simulação (Wokwi, via Wokwi para VS Code com compilação local em PlatformIO) e também testado no protótipo físico montado em sala, já com os módulos reais de solo e luminosidade (saída digital) e o DHT11. A validação completa de ponta a ponta no físico — sensores + backend automático rodando juntos — ainda está em andamento.

## Arquitetura específica

> **Histórico da N1.** Esta seção e a seguinte descrevem a primeira versão (tópicos em texto puro, broker público, `decisor.py`). O sistema atual está em [Como funciona hoje (N3)](#como-funciona-hoje-n3).

![Arquitetura do projeto](docs/arquitetura.png)

O ESP32 lê os três sensores (DHT11 via pino digital dedicado; solo e luminosidade via leitura digital dos módulos comparadores), classifica a condição da planta e publica telemetria em quatro tópicos MQTT:

```
grupo5/sensor/temperatura       (valor em °C)
grupo5/sensor/umidade_ar        (valor em %)
grupo5/sensor/umidade_solo      ("seco" ou "umido")
grupo5/sensor/luminosidade      ("escuro" ou "claro")
```

A classificação da condição (`adequado`, `atencao` ou `inadequado`) é publicada separadamente:

```
grupo5/status/condicao
```

O ESP32 assina dois tópicos de comando:

```
grupo5/comando/led      (payload: "on" ou "off")
grupo5/comando/buzzer   (payload: "on" ou "off")
```

Ao receber um comando, o firmware aciona o atuador correspondente e publica a confirmação do estado:

```
grupo5/status/led
grupo5/status/buzzer
```

**Decisão de arquitetura:** o firmware **não decide sozinho** ligar o LED/buzzer a partir da classificação — ele só publica a condição em `grupo5/status/condicao`. Quem decide acionar o atuador é algo de fora do dispositivo, mantendo a decisão desacoplada do sensor e sempre passando pela rede (MQTT) — critério de enquadramento IoT exigido pela disciplina (Edital N1, seção 3.4). Uma versão anterior do firmware acionava o LED/buzzer direto na mesma função que lia e classificava os sensores, sem passar por MQTT no caminho automático; isso foi identificado como falha de arquitetura (equivalente a automação local) e corrigido.

O LED e o buzzer também têm um timeout de segurança local: se não receberem um novo comando "on" dentro de 8 segundos (`DURACAO_ALERTA`), desligam sozinhos e publicam "off" no tópico de status. Isso não é uma decisão de classificação, só uma segurança contra ficar ligado indefinidamente por esquecimento.

### Backend de decisão automática (`backend/decisor.py`)

Além do firmware do ESP32, o projeto tem um pequeno backend em Python (`paho-mqtt` + interface gráfica em `tkinter`), que assume o papel de decidir quando acionar o atuador — sem precisar de ninguém publicando comando manualmente. Ele:

- se conecta ao mesmo broker (`broker.hivemq.com`);
- assina `grupo5/status/condicao`;
- a cada mensagem recebida, decide o comando (ligar LED e buzzer se `inadequado`; só o LED se `atencao`; desligar os dois se `adequado`) e publica de volta em `grupo5/comando/led`/`buzzer`;
- mostra tudo isso numa janela com log em tempo real (hora, condição recebida, comando publicado), em vez de precisar acompanhar por terminal.

Como o firmware publica a condição a cada 5 segundos (`INTERVALO_LEITURA`) e o backend publica o comando a cada vez que recebe uma condição (sem checar se mudou), o LED/buzzer recebem um novo "on" antes do timeout de 8 segundos expirar — o alerta fica ligado continuamente enquanto a condição for ruim, e só desliga de fato quando a condição volta a ser "adequado".

Esse backend é uma melhoria além do mínimo exigido pela N1 (que aceitaria decisão manual, via um cliente MQTT como o [cliente web da HiveMQ](https://www.hivemq.com/demos/websocket-client/)) — ele resolve o objetivo original do projeto (alertar sem precisar de alguém checando manualmente o tempo todo).

**Para rodar o backend:**
```bash
pip install paho-mqtt
python backend/decisor.py
```

## Estado atual e direção do upgrade (CP12 → N3)

**Estado atual (herdado da N1):** estação com 1 ESP32, 3 sensores (DHT11, solo e luminosidade com saída digital), LED e buzzer, telemetria MQTT e um backend de decisão em Python que fecha o ciclo comando → atuador → confirmação. Ainda **não** há banco de dados, histórico, interface web, login, broker próprio, orquestração, testes automatizados nem OTA, e o broker usado é público.

**Para onde o upgrade está indo (N3):** transformar o protótipo em um produto completo — o hardware mede, a rede transporta, o backend valida, decide e guarda, a interface informa e o usuário comanda, com confirmação do dispositivo. O piso da N3 (R1–R18) está conferido, requisito por requisito, no documento do CP12.

| Camada | Na N1 | Na N3 (proposta) |
|---|---|---|
| Hardware | 1 ESP32, sensores digitais, LED e buzzer | 2 nós ESP32 (`estacao-01` e `estacao-02`), sensor de solo analógico (ADC1) e atuador de irrigação (bomba + relé) além de LED/buzzer |
| Firmware | Leitura com `millis()`, reconexão bloqueante, sem configuração persistente | Laço não bloqueante, regra local sem rede, estado seguro, configuração em NVS, `deviceId`, LWT e OTA |
| MQTT | Broker público, payloads em texto puro | Mosquitto próprio (TLS se houver tempo), JSON versionado, `CONTRATOS.md` |
| Backend | Script `decisor` com interface de desktop | API que valida o contrato, regras remotas e comandos com `commandId` |
| Banco | Não existia | PostgreSQL (medições, comandos, usuários) |
| Interface | Cliente MQTT / log do script | Aplicação web com login, histórico, online/offline, alertas, comando e painel analítico |
| Operação | Execução manual | Docker Compose, notificação por e-mail, perfis de usuário, testes e CI |

### Arquitetura-alvo (N3)

![Arquitetura-alvo](docs/arquitetura_alvo.png)

![Fluxo do dado](docs/fluxo_dado.png)

![Fluxo do comando](docs/fluxo_comando.png)

### Documentos do upgrade

- [`CP12_EQUIPE-5_PARCIAL.pdf`](docs/CP12_EQUIPE-5_PARCIAL.pdf) — proposta de upgrade do CP12 (seções 1 a 4), entrega de 20/10/2026 (mesmo PDF entregue no Teams).
- As seções 5 a 9 (hardware calculado, fluxograma do firmware, contrato v1, tecnologias, cronograma e divisão) fazem parte da entrega completa do CP12 de 27/10/2026.

> Instruções de instalação e execução (R11): veja [Como rodar](#como-rodar).

- [`docs/Esquema_Eletrico_Por_No.jpeg`](docs/Esquema_Eletrico_Por_No.jpeg), [`docs/Fluxograma_Firmware.jpeg`](docs/Fluxograma_Firmware.jpeg), [`docs/mapa_de_pinos.md`](docs/mapa_de_pinos.md) e [`CONTRATOS.md`](CONTRATOS.md) (contrato MQTT v1): entregues no CP12 completo (27/10).

## Backlog atualizado

| Tarefa | Responsável | Status |
|---|---|---|
| Criar repositório | Daniela | Feito |
| Preencher README inicial | Daniela e João | Feito |
| Testar ESP32 com LED | Marcelo | Feito |
| Identificar sensor principal (DHT11 e LDR) | Willian | Feito |
| Testar sensor de umidade do solo | Willian | Feito (módulo real, saída digital) |
| Listar componentes necessários | Leonardo | Feito |
| Desenhar arquitetura inicial | Ana | Feito |
| Registrar primeiro risco técnico | Daniela | Feito |
| Implementar leitura do DHT11 (temperatura/umidade do ar) | Leonardo | Feito |
| Implementar leitura digital do sensor de solo e do LDR | Ana | Feito |
| Definir classificação (seco/úmido, escuro/claro, temperatura) | Ana | Feito |
| Conectar ESP32 ao Wi-Fi | João | Feito |
| Conectar ao broker MQTT e publicar telemetria | Willian | Feito |
| Implementar assinatura dos tópicos de comando (LED/buzzer) | Marcelo | Feito |
| Implementar confirmação de estado após comando | Leonardo | Feito |
| Implementar lógica de reconexão (Wi-Fi/broker) | Daniela | Feito |
| Corrigir modo automático para não acionar o atuador direto no firmware | Daniela | Feito |
| Trocar acionamento do buzzer para `tone()`/`noTone()` | Daniela | Feito |
| Mover credenciais de Wi-Fi para `secrets.h` (fora do repositório) | Daniela | Feito |
| Corrigir `DURACAO_ALERTA` para o alerta ficar contínuo | Daniela | Feito |
| Implementar backend de decisão automática (`backend/decisor.py`) | Daniela | Feito |
| Montar circuito completo em protoboard | Marcelo | Feito |
| Validar sensores + backend rodando juntos no protótipo físico | Todos | A fazer |
| Trocar Wi-Fi de teste pela rede real do dia da apresentação | João | A fazer |
| Testar demonstração funcional de ponta a ponta no hardware físico | João | A fazer |
| CP12 — seções 1 a 4 (PDF + repositório atualizado) | Todos | Feito |
| CP12 — seções 5 a 9 (hardware calculado, fluxograma, contrato v1, tecnologias, cronograma) | Todos | Feito (entrega de 27/10) |

## Riscos técnicos

### Risco 1 — Calibração dos sensores

Os módulos de umidade do solo e luminosidade têm sensibilidade ajustável fisicamente (potenciômetro no próprio módulo). Se a sensibilidade não estiver bem ajustada, a leitura digital (HIGH/LOW) pode não refletir corretamente a condição real do ambiente — por exemplo classificando solo levemente úmido como "seco". Esse ajuste precisa ser conferido fisicamente antes da demonstração, testando com o solo/luz em condições conhecidas.

### Risco 2 — Baud rate do monitor serial (identificado na Aula 06)

Em uma tentativa anterior de validar o firmware no protótipo físico, o monitor serial exibiu caracteres corrompidos em vez de texto legível, por uma provável incompatibilidade entre a taxa de transmissão do firmware (`Serial.begin(115200)`) e a configurada no monitor serial do computador usado naquele dia. O firmware foi posteriormente validado com sucesso, tanto em simulação (Wokwi) quanto no protótipo físico com o monitor serial configurado corretamente.
