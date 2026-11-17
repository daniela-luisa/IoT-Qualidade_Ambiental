# Cenários de Falha Testados (R10)

> **Instruções de preenchimento:**  
> As seções "Observado" e "Evidência" devem ser preenchidas durante os ensaios
> com os dispositivos físicos em 17/11. Não escreva o que não aconteceu.  
> Os comandos de apoio abaixo já foram verificados com broker e simulador reais.

---

## Cenário 1 — Queda do Wi-Fi do dispositivo

### Como provocar

1. Com o simulador rodando e a estação `estacao-01` online, desligue o Wi-Fi
   do dispositivo (ou, no Wokwi, interrompa a conectividade de rede).
2. Aguarde o LWT (Last Will and Testament) chegar ao broker. O MQTT publica
   automaticamente a mensagem de status `offline` configurada na conexão.

**Comando de apoio (verificar no banco):**

```sql
-- Confirma que o dispositivo ficou offline e o alerta foi aberto
SELECT online, ultimo_contato FROM dispositivos WHERE id = 'estacao-01';
SELECT tipo, mensagem, aberto_em, resolvido_em
  FROM alertas WHERE dispositivo_id = 'estacao-01' ORDER BY aberto_em DESC LIMIT 3;
SELECT motivo, enviada, erro, criado_em
  FROM notificacoes WHERE dispositivo_id = 'estacao-01' ORDER BY criado_em DESC LIMIT 3;
```

### Comportamento esperado

- O campo `online` do dispositivo passa para `false`.
- Um alerta `dispositivo_offline` é aberto (ou reutilizado se já existia).
- Uma tentativa de e-mail é registrada em `notificacoes`
  (`enviada = true` se SMTP configurado, `erro = 'smtp_nao_configurado'` caso contrário).
- Quando o Wi-Fi volta, o dispositivo publica `status: online` e o alerta é
  resolvido (`resolvido_em` preenchido).

### Observado

> _(preencher em 17/11 com o sistema físico)_

### Evidência

> _(print da aba Actions / log do backend / linhas do banco — inserir em 17/11)_

---

## Cenário 2 — Broker MQTT indisponível

### Como provocar

1. Com o sistema funcionando, derrube o broker:

   ```bash
   docker compose stop mosquitto
   ```

2. Tente enviar um comando pela API:

   ```bash
   curl -s -X POST http://localhost:8000/api/comandos \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"dispositivoId":"estacao-01","alvo":"bomba","duracaoS":5}' | jq .
   ```

3. Suba o broker novamente e tente outro comando:

   ```bash
   docker compose start mosquitto
   # Aguarde ~6 s para reconexão automática, depois repita o curl acima.
   ```

**Comando de apoio:**

```sql
-- Confirma que o primeiro comando ficou como 'falhou'
SELECT comando_id, estado, motivo, enviado_em
  FROM comandos ORDER BY enviado_em DESC LIMIT 5;
```

### Comportamento esperado

- Com o broker fora, a API retorna **HTTP 503** com
  `{"codigo": "broker_indisponivel"}`.
- O comando é gravado no banco com `estado = 'falhou'` e
  `motivo = 'broker_indisponivel'`.
- Após o broker subir, backend e simuladores reconectam automaticamente
  (cerca de 6 s — `reconnect_delay_set(min=1, max=5)`).
- Um novo comando enviado após a reconexão é publicado normalmente e
  segue o ciclo `enviado → recebido → executado → confirmado`.

### Observado

> _(preencher em 17/11 com o sistema físico)_

### Evidência

> _(print do terminal com o 503, print da aba Actions ou log do backend mostrando reconexão, linhas do banco — inserir em 17/11)_

---

## Cenário 3 — Falha de sensor

### Como provocar

**Opção A — simulador com flag:**

```bash
python tests/simulador_dispositivo.py \
  --device estacao-01 --host localhost --falha-sensor
```

**Opção B — payload manual via mosquitto_pub:**

```bash
# Valor nulo
mosquitto_pub -h localhost -t "grupo5/estacao-01/telemetria/temperatura" \
  -m '{"v":1,"deviceId":"estacao-01","ts":null,"valor":null}'

# Valor fora da faixa (temperatura máxima é 50 °C)
mosquitto_pub -h localhost -t "grupo5/estacao-01/telemetria/temperatura" \
  -m '{"v":1,"deviceId":"estacao-01","ts":null,"valor":99}'

# Duração inválida num comando (bomba aceita 1–15 s)
curl -s -X POST http://localhost:8000/api/comandos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dispositivoId":"estacao-01","alvo":"bomba","duracaoS":99}' | jq .
```

**Comando de apoio:**

```sql
-- Confirma evento falha_sensor e ausência de leitura inválida
SELECT tipo, detalhe, criado_em FROM eventos
  WHERE tipo = 'falha_sensor' ORDER BY criado_em DESC LIMIT 5;
SELECT count(*) FROM leituras WHERE valor > 50 AND grandeza = 'temperatura';
```

### Comportamento esperado

- Valor `null` ou fora da faixa: o backend registra um evento `falha_sensor`
  com o detalhe da grandeza e do valor recebido. Nenhuma leitura é gravada
  em `leituras`.
- Comando com `duracaoS: 99` (bomba aceita 1–15 s): API retorna **HTTP 422**
  com `{"codigo": "valor_fora_da_faixa"}`. Nenhum comando é gravado.
- O sistema continua processando mensagens subsequentes normalmente
  (a falha não derruba o handler).

### Observado

> _(preencher em 17/11 com o sistema físico)_

### Evidência

> _(print do terminal com o 422, linhas da tabela eventos, confirmação de que leituras não foram gravadas — inserir em 17/11)_

---

## Cenário 4 — Comando inválido ou duplicado

### Como provocar

**Comando inválido (alvo desconhecido):**

```bash
curl -s -X POST http://localhost:8000/api/comandos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dispositivoId":"estacao-01","alvo":"ventilador"}' | jq .
```

**Comando duplicado (intertravamento de intervalo):**

```bash
# Primeiro comando — deve ser aceito
curl -s -X POST http://localhost:8000/api/comandos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dispositivoId":"estacao-01","alvo":"bomba","duracaoS":5}' | jq .

# Segundo comando imediato — deve ser rejeitado pelo firmware com motivo
# 'intertravamento_intervalo' via mensagem de confirmação
curl -s -X POST http://localhost:8000/api/comandos \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dispositivoId":"estacao-01","alvo":"bomba","duracaoS":5}' | jq .
```

**Comando de apoio:**

```sql
-- Verifica estados e motivos dos dois comandos
SELECT comando_id, estado, motivo, enviado_em FROM comandos
  ORDER BY enviado_em DESC LIMIT 5;

-- Verifica evento de motivo fora do catálogo (se firmware mandar texto novo)
SELECT tipo, detalhe FROM eventos
  WHERE tipo = 'mensagem_invalida' ORDER BY id DESC LIMIT 5;
```

### Comportamento esperado

- Alvo inválido (`ventilador`): API retorna **HTTP 422** com
  `{"codigo": "alvo_invalido"}`. Nada é gravado no banco.
- Segundo comando de bomba em sequência rápida: o firmware rejeita via
  mensagem de confirmação com `resultado: "rejeitado"` e
  `motivo: "intertravamento_intervalo"`. O backend grava o comando como
  `estado = 'rejeitado'` com o motivo correspondente.
- Se o firmware enviar um motivo fora do catálogo dos 8 motivos definidos
  no contrato, o backend registra o estado mas também cria um evento
  `mensagem_invalida` com o motivo recebido.

### Observado

> _(preencher em 17/11 com o sistema físico)_

### Evidência

> _(print do terminal com o 422, linhas do banco mostrando estado 'rejeitado' e o motivo — inserir em 17/11)_

---

## Resumo dos cenários

| # | Cenário                        | Código HTTP / estado banco          | Motivo / tipo evento          |
|---|--------------------------------|-------------------------------------|-------------------------------|
| 1 | Queda de Wi-Fi                 | `online = false`                    | alerta `dispositivo_offline`  |
| 2 | Broker indisponível            | 503 / `estado = 'falhou'`           | `broker_indisponivel`         |
| 3 | Falha de sensor                | 422 (comando) / evento `falha_sensor` | `valor_fora_da_faixa`        |
| 4 | Comando inválido ou duplicado  | 422 (inválido) / `estado = 'rejeitado'` | `intertravamento_intervalo` |

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
