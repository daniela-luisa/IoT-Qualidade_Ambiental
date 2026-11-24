# Cenários de Falha Testados (R10)

> **Como preencher:** rode cada cenário com a **placa física** (`estacao-01`) e o sistema no ar
> (`docker compose up -d`). Em "Observado", escreva só o que aconteceu de fato, com data e quem executou.
> Em "Evidência", cole prints e logs: salve os arquivos em [`docs/evidencias/`](evidencias/) e coloque o link
> aqui. Cenário sem evidência não conta para o R10. Na defesa, o professor escolhe um destes para repetir ao vivo.

**Antes de começar (uma vez):**

```bash
docker compose up -d                       # broker, backend, banco
docker compose logs -f backend             # deixe aberto em outro terminal (evidência do backend)
# token para os curl (admin do .env):
TOKEN=$(curl -s -X POST http://localhost:8000/api/login -H "Content-Type: application/json" \
  -d '{"email":"SEU_ADMIN_EMAIL","senha":"SUA_SENHA"}' | jq -r .token)
# consultas no banco:
docker exec -it macp-postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Deixe o **Serial Monitor** (115200) aberto e o **painel** (http://localhost:8000) logado como admin.

---

## Cenário 1 — Queda do Wi-Fi do dispositivo

### Como provocar

1. Com a `estacao-01` online no painel, **desligue a rede Wi-Fi** que a placa usa (desligue o roteador/hotspot
   ou afaste a placa) por **1 a 2 minutos**.
2. Observe a placa e o painel. Depois religue a rede.

**Consultas de apoio:**

```sql
-- ficou offline e o alerta abriu (e depois resolveu)
SELECT online, ultimo_contato FROM dispositivos WHERE id = 'estacao-01';
SELECT tipo, mensagem, aberto_em, resolvido_em
  FROM alertas WHERE dispositivo_id = 'estacao-01' ORDER BY aberto_em DESC LIMIT 3;
-- depois que voltou: leituras do periodo offline chegaram com a hora em que foram medidas
SELECT grandeza, valor, medido_em, recebido_em
  FROM leituras WHERE dispositivo_id = 'estacao-01' AND recebido_em - medido_em > interval '30 seconds'
  ORDER BY medido_em DESC LIMIT 10;
SELECT tipo, detalhe, criado_em
  FROM eventos WHERE dispositivo_id = 'estacao-01' AND detalhe LIKE '%offline%' ORDER BY criado_em DESC LIMIT 10;
```

### Comportamento esperado

- **Placa (regra local, sem rede):** continua lendo os sensores e irrigando sozinha se o solo secar. O LED fica
  **aceso** se a condição for `inadequado`, ou **piscando** (sem conexão) se não for. A serial mostra
  `[historico] sem broker: N leitura(s) ... guardados`.
- **Backend:** em uns 20 s (keepalive de 15 s) o broker publica o Last Will `offline`; o dispositivo fica
  `online = false` e abre o alerta `dispositivo_offline`. O cartão no painel fica "offline".
- **Volta:** a placa reconecta sozinha (sem reinício), publica `status: online`, o alerta é resolvido e ela
  reenvia as leituras e os eventos guardados com a hora original (serial: `[historico] reenvio concluido`).
  O gráfico do painel não fica com buraco.

### Observado

> _Data / quem executou:_
> _O que aconteceu na placa, no painel e no banco:_

### Evidência

> _Serial com `[historico]` e `[mqtt] conectado`; print do painel offline e depois online; resultado das
> consultas acima. Arquivos em `docs/evidencias/c1-*`._

---

## Cenário 2 — Broker MQTT indisponível

### Como provocar

1. Com o sistema funcionando, derrube o broker:

   ```bash
   docker compose stop mosquitto
   ```

2. Tente enviar um comando pela API (ou pelo painel):

   ```bash
   curl -s -X POST http://localhost:8000/api/comandos \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"dispositivoId":"estacao-01","alvo":"led","duracaoS":5}' | jq .
   ```

3. Suba o broker de novo, espere uns 10 s e repita o comando:

   ```bash
   docker compose start mosquitto
   ```

**Consulta de apoio:**

```sql
SELECT comando_id, estado, motivo, enviado_em FROM comandos ORDER BY enviado_em DESC LIMIT 5;
```

### Comportamento esperado

- **API:** com o broker fora, responde **HTTP 503** `{"codigo": "broker_indisponivel"}` e o comando fica no
  banco como `estado = 'falhou'`, `motivo = 'broker_indisponivel'`.
- **Placa:** a serial mostra `[mqtt] falhou rc=...` a cada 5 s; a regra local continua valendo e as leituras são
  guardadas para reenvio (como no cenário 1).
- **Volta:** backend e placa reconectam sozinhos, sem reinício. O comando repetido segue
  `enviado → recebido → executado → confirmado` e o LED acende.

### Observado

> _Data / quem executou:_
> _O que aconteceu:_

### Evidência

> _Print do 503; log do backend reconectando; serial com `[mqtt] conectado`; linhas da tabela `comandos`
> (`falhou` e depois `confirmado`). Arquivos em `docs/evidencias/c2-*`._

---

## Cenário 3 — Falha de sensor

### Como provocar

**Na placa (principal):** com a placa ligada, **solte o fio AO do sensor de solo** (GPIO 35). Para testar o DHT11,
solte o fio de dados (GPIO 14). Depois de observar, religue o fio.

**Payload inválido direto no broker (complementar, para mostrar a validação do backend):**

```bash
docker exec macp-mosquitto mosquitto_pub -t "grupo5/estacao-01/telemetria/temperatura" \
  -m '{"v":1,"deviceId":"estacao-01","ts":null,"valor":99}'
```

**Consultas de apoio:**

```sql
SELECT tipo, detalhe, criado_em FROM eventos WHERE tipo = 'falha_sensor' ORDER BY criado_em DESC LIMIT 5;
SELECT modo FROM dispositivos WHERE id = 'estacao-01';
SELECT count(*) FROM leituras WHERE grandeza = 'temperatura' AND valor > 50;   -- deve ser 0
```

### Comportamento esperado

- **Placa:** a serial mostra `solo=nan% (raw ... INVALIDO)` e `[solo] sem leitura valida: bomba bloqueada`.
  Depois de **3 leituras seguidas com falha** (~15 s) ela entra em **modo seguro**: bomba desligada, LED
  **piscando**, e `bomba on` é rejeitado com `modo_seguro`.
- **Backend:** a telemetria chega com `valor: null` e vira evento `falha_sensor` (no máximo 1 por minuto por
  grandeza); nenhuma leitura inválida é gravada. O cartão mostra "Modo seguro".
- **Volta:** ao religar o fio, a primeira leitura válida tira a placa do modo seguro (volta a `automatico`).
- **Payload com 99 °C:** vira `falha_sensor` (fora de 0 a 50 °C) e não é gravado.

### Observado

> _Data / quem executou:_
> _O que aconteceu:_

### Evidência

> _Serial com `INVALIDO` e `modo=seguro`; print do cartão em modo seguro; comando de bomba rejeitado com
> `modo_seguro` na tabela de comandos; linhas de `eventos`. Arquivos em `docs/evidencias/c3-*`._

---

## Cenário 4 — Comando inválido ou duplicado

### Como provocar

**Inválido na API (alvo desconhecido e duração fora da faixa):**

```bash
curl -s -X POST http://localhost:8000/api/comandos \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dispositivoId":"estacao-01","alvo":"ventilador"}' | jq .
curl -s -X POST http://localhost:8000/api/comandos \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"dispositivoId":"estacao-01","alvo":"bomba","duracaoS":99}' | jq .
```

**Bloqueado por intertravamento (bomba duas vezes seguidas, com o solo na terra):**

```bash
for i in 1 2; do
  curl -s -X POST http://localhost:8000/api/comandos \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"dispositivoId":"estacao-01","alvo":"bomba","duracaoS":3}' | jq .
done
```

**Duplicado (mesmo `commandId` entregue duas vezes à placa):**

```bash
for i in 1 2; do
  docker exec macp-mosquitto mosquitto_pub -t "grupo5/estacao-01/comando" \
    -m '{"v":1,"commandId":"c-20261124-9999","alvo":"led","acao":"on","duracaoS":3,"origem":"usuario"}'
done
```

**Consultas de apoio:**

```sql
SELECT comando_id, estado, motivo, enviado_em FROM comandos ORDER BY enviado_em DESC LIMIT 5;
SELECT tipo, detalhe FROM eventos WHERE tipo = 'mensagem_invalida' ORDER BY id DESC LIMIT 5;
```

### Comportamento esperado

- **API:** `ventilador` → **HTTP 422** `alvo_invalido`; `duracaoS: 99` → **HTTP 422** `valor_fora_da_faixa`.
  Nada é publicado nem gravado.
- **Intertravamento:** o primeiro comando de bomba é confirmado; o segundo é rejeitado pela placa com
  `intertravamento_intervalo` e aparece como **Rejeitado** com o motivo na tabela de comandos do painel.
- **Duplicado:** a serial mostra o primeiro aceito e o segundo `[cmd] rejeitado: comando_duplicado`.
  Como esse comando foi publicado à mão (não passou pela API), o backend registra as confirmações como
  `mensagem_invalida` ("confirmacao de comando desconhecido"): é a validação do backend agindo também.
- Motivo fora do catálogo (9 motivos do contrato) vira evento `mensagem_invalida`.

### Observado

> _Data / quem executou:_
> _O que aconteceu:_

### Evidência

> _Prints dos 422; print da tabela de comandos com "Rejeitado — intertravamento"; serial com
> `comando_duplicado`. Arquivos em `docs/evidencias/c4-*`._

---

## Resumo dos cenários

| # | Cenário | O que comprova | Resultado esperado principal |
|---|---------|----------------|------------------------------|
| 1 | Queda de Wi-Fi | regra local, LWT, reconexão, histórico offline | `online = false` + alerta; volta sozinho e reenvia o histórico |
| 2 | Broker indisponível | backend e placa sobrevivem ao broker fora | 503 / `falhou`; reconexão automática |
| 3 | Falha de sensor | validação e estado seguro | modo seguro, bomba bloqueada, evento `falha_sensor` |
| 4 | Comando inválido ou duplicado | validação e intertravamento | 422; `rejeitado` com motivo; `comando_duplicado` |

---

## Por que o banco de teste é apagado a cada teste?

O `conftest.py` executa `DROP SCHEMA public CASCADE; CREATE SCHEMA public`
seguido de uma re-aplicação completa do `schema.sql` antes de cada teste.
Isso garante **isolamento total**: nenhum teste herda dados de outro, e um
teste que insere linhas inválidas não polui os seguintes. É seguro porque
`TEST_DATABASE_URL` aponta para `macp_test` — um banco descartável criado
só para os testes. O banco de produção (`macp`) nunca é tocado.

## O que a PonteFalsa substitui?

Em produção, `PonteMQTT` abre uma conexão TCP com o broker e usa a biblioteca
`paho-mqtt` para publicar e assinar tópicos. Nos testes, `PonteFalsa` substitui
esse objeto: o método `publicar()` simplesmente acrescenta a mensagem à lista
`publicados` em vez de transmitir por rede. Com isso os testes:

- Rodam sem nenhum broker ativo.
- Terminam em segundos (sem latência de rede).
- Permitem inspecionar exatamente o que teria sido publicado
  (`topico`, `payload`, `qos`) e verificar o contrato do payload.
- Simulam broker desconectado setando `ponte.conectado = False`.

## Senhas fictícias no repositório

Os arquivos de teste usam senhas como `admin123` e `operador123`. Elas
existem **só no banco `macp_test`**, que é recriado do zero a cada execução
e nunca tem conexão com o ambiente de produção. O código que as define
(`conftest.py`) chama `auth.hash_senha()` antes de inserir — só o hash
bcrypt (`$2b$...`) vai para o banco, nunca o texto plano. O teste
`test_senhas_ficam_so_como_hash` verifica exatamente isso. Se o professor
buscar a string `admin123` no repositório, ela aparece apenas em
`conftest.py` como argumento de função de hash, nunca em SQL direto nem
em variável de ambiente commitada.
