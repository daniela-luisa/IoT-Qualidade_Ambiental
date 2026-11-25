# Contrato MQTT v1.1

| Versão | Data | O que mudou |
|---|---|---|
| **v1** | 27/10/2026 | Contrato congelado no CP13: telemetria, estado, status (LWT), comando e confirmação. |
| **v1.1** | 24/11/2026 | Só **acréscimos**, compatíveis com quem fala v1 (por isso o campo `v` dos payloads continua `1`): campo opcional `recursos` no `estado` (fw 1.4.0); tópico `evento` (histórico offline no fw 1.5.0, botão BOOT no fw 1.6.0); parâmetro de config `bombaBloqueada` e o campo de mesmo nome no `estado` (fw 1.6.0); motivo de rejeição `bomba_bloqueada` (fw 1.6.0); duração padrão da bomba de 5 s para 3 s. |

**Por que `v` continua `1`:** nenhum campo existente mudou de nome, tipo ou significado. Um firmware v1 continua
sendo entendido pelo backend (sem `recursos` = tem todos os atuadores; sem `bombaBloqueada` = o backend não manda
bloqueio), e um backend v1 ignora o tópico `evento` e os campos novos. O `v` só sobe numa mudança incompatível.

Prefixo `grupo5/{deviceId}/`, com `deviceId` = `estacao-01` ou `estacao-02` (padrão `estacao-NN`). Payloads em
JSON UTF-8 com `v` (versão), `deviceId` e `ts` (ISO 8601 com fuso, ou `null` sem NTP).

## Tópicos, QoS e retain

| Tópico | Sentido | QoS | Retain | Por quê |
|--------|---------|-----|--------|---------|
| `telemetria/{grandeza}` | dispositivo → backend | 0 | não | Chega uma leitura nova a cada 5 s: perder uma não faz falta e QoS 0 não gera fila nem retransmissão. Sem retain porque uma leitura velha não pode aparecer como atual para quem assina depois. |
| `estado` | dispositivo → backend | 0 (publica) / 1 (assina) | **sim** | Retain: quem conecta (backend reiniciado, MQTT Explorer) recebe na hora o último estado da placa. É republicado a cada 30 s e em toda mudança, então uma perda se corrige sozinha. |
| `status` (LWT) | dispositivo/broker → backend | 0 (`online`) / 1 (`offline`, Last Will) | **sim** | O broker publica `offline` sozinho se a placa cair (Last Will, QoS 1). Retain para que qualquer um saiba se a estação está online sem esperar a próxima mensagem. |
| `comando` | backend → dispositivo | 1 | não | O comando precisa chegar (QoS 1 = pelo menos uma vez). Uma entrega repetida é barrada pelo `commandId` (`comando_duplicado`). **Nunca retain:** a placa executaria um comando velho (ligar a bomba) ao reconectar. |
| `confirmacao` | dispositivo → backend | 0 (publica) / 1 (assina) | não | Cada confirmação é de um comando só, então retain não faz sentido. Se uma se perder, o backend marca o comando como `expirado` depois de `COMANDO_TIMEOUT_S` (30 s): o ciclo nunca fica pendurado. |
| `evento` | dispositivo → backend | 0 | não | Mudanças que aconteceram sem conexão (reenviadas ao reconectar) e o botão BOOT. Retain não faz sentido para eventos passados. |

**QoS efetivo da placa:** a biblioteca do firmware (PubSubClient) **só publica em QoS 0**. O único QoS 1 do lado da
placa é o Last Will (`status offline`). O backend assina tudo com QoS 1, mas a entrega placa → broker é QoS 0.
Isso é compensado pelo desenho acima: estado periódico com retain, timeout do comando e `commandId`. No MQTT
Explorer, as mensagens da placa aparecem com QoS 0.

`grandeza`: `temperatura` (°C), `umidade_ar` (%), `umidade_solo` (%), `luminosidade` (%).

## Exemplos (um por tópico)

```json
// telemetria/temperatura
{"v":1,"deviceId":"estacao-01","ts":"2026-11-24T09:15:00-03:00","grandeza":"temperatura","valor":24.6,"unidade":"°C"}
// estado (estação com bomba)
{"v":1,"deviceId":"estacao-01","ts":"2026-11-24T09:15:05-03:00","modo":"automatico","condicao":"atencao","bomba":"off","led":"off","buzzer":"off","fw":"1.6.0","recursos":{"sensores":["temperatura","umidade_ar","umidade_solo","luminosidade"],"atuadores":["bomba","led","buzzer"]},"limiarSoloSecoPct":30,"intervaloMinS":60,"duracaoMaxS":15,"bombaBloqueada":0}
// estado (estação sem bomba)
{"v":1,"deviceId":"estacao-02","ts":"2026-11-24T09:15:05-03:00","modo":"automatico","condicao":"inadequado","led":"on","buzzer":"off","fw":"1.6.0","recursos":{"sensores":["temperatura","umidade_ar","umidade_solo","luminosidade"],"atuadores":["led","buzzer"]},"limiarSoloSecoPct":30,"intervaloMinS":60,"duracaoMaxS":15}
// status (online ao conectar; offline publicado pelo broker como Last Will)
{"v":1,"deviceId":"estacao-01","status":"online"}
{"v":1,"deviceId":"estacao-01","status":"offline"}
// comando (atuador)
{"v":1,"commandId":"c-20261124-0007","ts":"2026-11-24T09:20:00-03:00","alvo":"bomba","acao":"on","duracaoS":3,"origem":"regra"}
// comando (configuração)
{"v":1,"commandId":"c-20261124-0012","ts":"2026-11-24T10:20:00-03:00","alvo":"config","acao":"set","bombaBloqueada":1,"origem":"regra"}
// confirmacao (aceito e executado / rejeitado com motivo)
{"v":1,"deviceId":"estacao-01","commandId":"c-20261124-0007","ts":"2026-11-24T09:20:01-03:00","etapa":"executado","resultado":"ok","motivo":null}
{"v":1,"deviceId":"estacao-01","commandId":"c-20261124-0008","ts":"2026-11-24T09:20:30-03:00","etapa":"recebido","resultado":"rejeitado","motivo":"intertravamento_intervalo"}
// evento (guardado sem conexão / botão BOOT)
{"v":1,"deviceId":"estacao-01","ts":"2026-11-24T10:02:15-03:00","tipo":"bomba","detalhe":"on (modo automatico, offline)"}
{"v":1,"deviceId":"estacao-01","ts":"2026-11-24T10:30:00-03:00","tipo":"alarme","detalhe":"silenciado no botao da placa"}
```

## Comandos aceitos e faixas

| `alvo` | `acao` | Campos | Faixa |
|---|---|---|---|
| `bomba` | `on` / `off` | `duracaoS` (só no `on`) | 1 a 15 s, padrão 3; com `origem` `regra`, nunca acima de `duracaoMaxS` |
| `led` | `on` / `off` | `duracaoS` (só no `on`) | 1 a 60 s, padrão 8 |
| `buzzer` | `on` / `off` | `duracaoS` (só no `on`) | 1 a 60 s, padrão 8 |
| `config` | `set` | um ou mais parâmetros abaixo | todos **inteiros** (o firmware rejeita `30.5` com `valor_fora_da_faixa`) |

Parâmetros de `config` (gravados na NVS, sobrevivem à reinicialização, e devolvidos no `estado`):

| Parâmetro | Faixa | Para que serve |
|---|---|---|
| `limiarSoloSecoPct` | 10 a 80 | abaixo disso o solo é "seco" |
| `intervaloMinS` | 30 a 3600 | tempo mínimo entre duas irrigações |
| `duracaoMaxS` | 1 a 15 | teto de duração da bomba |
| `bombaBloqueada` | 0 ou 1 | 1 = bomba travada (reservatório vazio?): não irriga sozinha e rejeita `bomba on` |

`origem`: `usuario` (pessoa pelo painel, coloca a placa em modo manual por 60 s) ou `regra` (backend).

Faixas de telemetria aceitas pelo backend: temperatura 0 a 50 °C; umidade_ar 20 a 90 %; umidade_solo e
luminosidade 0 a 100 %. Fora disso (ou `valor: null`) vira evento `falha_sensor` e a leitura não é gravada.

## Confirmação e ciclo do comando
`commandId` (formato `c-AAAAMMDD-NNNN`), `etapa` ∈ {`recebido`, `executado`} e `resultado` ∈ {`ok`, `rejeitado`},
com `motivo` quando rejeitado.

enviado (backend registra) → recebido (firmware aceita ou rejeita) → executado (atuador agiu) → confirmado (o
backend vê o `estado` publicado logo depois e a interface reflete). Finais alternativos: `rejeitado` (com motivo),
`falhou` (broker fora na hora de publicar) e `expirado` (sem resposta em `COMANDO_TIMEOUT_S`).

## Catálogo de códigos de erro

**Motivos de rejeição do firmware** (`confirmacao.motivo`):

| Código | Quando |
|---|---|
| `intertravamento_intervalo` | só `origem` `regra`: a bomba está ligada ou ligou há menos de `intervaloMinS` (por qualquer origem) |
| `intertravamento_duracao` | só `origem` `regra`: `duracaoS` acima de `duracaoMaxS` |
| `modo_seguro` | placa em modo seguro, ou sem leitura válida do solo (bomba) |
| `comando_duplicado` | `commandId` já recebido |
| `alvo_invalido` | alvo desconhecido ou atuador que a placa não tem |
| `valor_fora_da_faixa` | `acao` ou `duracaoS` ou parâmetro de config fora da faixa |
| `json_invalido` | payload não é JSON |
| `versao_nao_suportada` | `v` diferente de 1 |
| `bomba_bloqueada` | (v1.1) bomba travada pelo backend, reservatório vazio |

**Mensagens inválidas no backend** (descartadas e registradas como evento `mensagem_invalida`, ou `falha_sensor`):
`json_invalido`, `versao_nao_suportada`, `device_id_divergente` (tópico ≠ payload), `topico_desconhecido`,
`grandeza_invalida`, `grandeza_divergente`, `falha_sensor`, `status_invalido`, `command_id_invalido`,
`etapa_invalida`, `resultado_invalido`, `evento_invalido` e dispositivo desconhecido.

**Erros da API** (campo `codigo` da resposta): `alvo_invalido` e `valor_fora_da_faixa` (422),
`dispositivo_desconhecido` (404), `dispositivo_offline` (409), `broker_indisponivel` (503), `sem_permissao` (403),
`credenciais_invalidas` (401), `alerta_automatico` (422), `alerta_ja_resolvido` (409).

## Recursos da estação (campo opcional do `estado`, v1.1)
`recursos.sensores` ⊂ grandezas e `recursos.atuadores` ⊂ {bomba, led, buzzer}: o que a placa tem montado.
O backend guarda, recusa comando para atuador ausente (`alvo_invalido`, sem publicar) e a interface só mostra
o que existe. Estação **sem bomba** não manda o campo `bomba` no estado; com o solo seco, a regra do backend
aciona `led` + `buzzer` (origem `regra`) no lugar da bomba. Estado sem `recursos` (firmware antigo) = tem tudo.

## Tópico `evento` (v1.1)
`tipo` ∈ {`bomba`, `modo`, `condicao`, `alarme`} e `detalhe` em texto (até 200 caracteres). O backend grava em
`eventos` com `criado_em` = `ts`.

- **Histórico offline (fw 1.5.0):** sem broker (Wi-Fi caiu ou broker fora), a placa guarda na RAM as leituras
  (até 2 h, a cada 5 s) e as mudanças de bomba, modo e condição. Ao reconectar, com o relógio acertado pelo NTP,
  reenvia tudo **com o `ts` de quando aconteceu**: as leituras pelos tópicos normais de `telemetria` (o backend
  grava `medido_em` = `ts`) e as mudanças pelo tópico `evento` (`detalhe` terminado em `offline`). Reiniciar a
  placa sem conexão perde o que estava guardado.
- **Botão BOOT (fw 1.6.0):** `tipo` `alarme` quando alguém aperta o botão BOOT. A placa apaga LED/buzzer
  acionados por comando e o backend para de repetir o alarme do alerta aberto (reservatório vazio, ou solo seco
  na estação sem bomba).

## Timestamp
ISO 8601 com fuso. Sem hora sincronizada por NTP, `ts` vai como `null` (indicação explícita de hora não
sincronizada) e o backend usa a hora de chegada.
