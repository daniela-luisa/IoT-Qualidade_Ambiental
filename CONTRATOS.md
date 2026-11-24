# Contrato MQTT v1 (congelado em 27/10/2026)

Prefixo `grupo5/{deviceId}/`, com `deviceId` = `estacao-01` ou `estacao-02`. Payloads em JSON UTF-8 com o campo `v` (versão).

| Tópico | Sentido | QoS | Retain |
|--------|---------|-----|--------|
| `grupo5/{deviceId}/telemetria/{grandeza}` | dispositivo para backend | 0 | não |
| `grupo5/{deviceId}/estado` | dispositivo para backend | 1 | sim |
| `grupo5/{deviceId}/status` (LWT) | dispositivo para backend | 1 | sim |
| `grupo5/{deviceId}/comando` | backend para dispositivo | 1 | não |
| `grupo5/{deviceId}/confirmacao` | dispositivo para backend | 1 | não |

`grandeza`: temperatura, umidade_ar, umidade_solo, luminosidade.

## Exemplos

```json
{"v":1,"deviceId":"estacao-01","ts":"2026-11-24T09:15:00-03:00","grandeza":"temperatura","valor":24.6,"unidade":"°C"}
{"v":1,"deviceId":"estacao-01","ts":"2026-11-24T09:15:05-03:00","modo":"automatico","condicao":"atencao","bomba":"off","led":"off","buzzer":"off","fw":"1.0.0"}
{"v":1,"deviceId":"estacao-02","ts":"2026-11-24T09:15:05-03:00","modo":"automatico","condicao":"inadequado","led":"on","buzzer":"off","fw":"1.4.0","recursos":{"sensores":["temperatura","umidade_ar","umidade_solo","luminosidade"],"atuadores":["led","buzzer"]}}
{"v":1,"commandId":"c-20261124-0007","ts":"2026-11-24T09:20:00-03:00","alvo":"bomba","acao":"on","duracaoS":5,"origem":"regra"}
{"v":1,"deviceId":"estacao-01","commandId":"c-20261124-0007","ts":"2026-11-24T09:20:01-03:00","etapa":"executado","resultado":"ok","motivo":null}
{"v":1,"deviceId":"estacao-01","commandId":"c-20261124-0008","ts":"2026-11-24T09:20:30-03:00","etapa":"recebido","resultado":"rejeitado","motivo":"intertravamento_intervalo"}
{"v":1,"deviceId":"estacao-01","status":"offline"}
```

## Faixas
- temperatura 0 a 50 °C; umidade_ar 20 a 90 %; umidade_solo e luminosidade 0 a 100 %.
- `bomba`: `duracaoS` 1 a 15 (padrão 5). `led` e `buzzer`: `duracaoS` 1 a 60 (padrão 8).
- `config`: `limiarSoloSecoPct` 10 a 80, `intervaloMinS` 30 a 3600, `duracaoMaxS` 1 a 15 — todos **inteiros** (o firmware rejeita `30.5` com `valor_fora_da_faixa`).

## Recursos da estação (campo opcional do `estado`, desde o fw 1.4.0)
`recursos.sensores` ⊂ grandezas e `recursos.atuadores` ⊂ {bomba, led, buzzer}: o que a placa tem montado.
O backend guarda, recusa comando para atuador ausente (`alvo_invalido`, sem publicar) e a interface só mostra
o que existe. Estação **sem bomba** não manda o campo `bomba` no estado; com o solo seco, a regra do backend
aciona `led` + `buzzer` (origem `regra`) no lugar da bomba. Estado sem `recursos` (firmware antigo) = tem tudo.

## Ciclo do comando
enviado (backend registra) → recebido (firmware aceita ou rejeita) → executado (atuador agiu) → confirmado (estado publicado e refletido). Todo comando tem `commandId`.

## Catálogo de motivos de rejeição
intertravamento_intervalo, intertravamento_duracao, modo_seguro, comando_duplicado, alvo_invalido, valor_fora_da_faixa, json_invalido, versao_nao_suportada.

## Timestamp
ISO 8601 com fuso. Sem hora sincronizada por NTP, `ts` vai como `null` e o backend usa a hora de chegada.
