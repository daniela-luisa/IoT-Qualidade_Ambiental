# Projeto IoT - Grupo 8 - Monitoramento Ambiental para Crescimento de Plantas

## Integrantes

- Ana Beatriz Pedrozo
- Daniela Luisa da Conceição
- Marcelo Gustavo Eger
- João Henrique Souza Rocha
- Leonardo André Ferreira
- Willian Squena

## Família temática

Grupo 8 - Qualidade Ambiental.

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

## Componentes previstos

- ESP32
- Sensor de temperatura e umidade do ar (DHT22)
- Sensor de umidade do solo (capacitivo, v1.2)
- Sensor de luminosidade (LDR)
- LED RGB
- Buzzer

## Arquitetura inicial

```
Sensores (DHT22 + Umidade do solo + LDR) -> ESP32 -> Wi-Fi -> MQTT -> comando -> atuador (LED + buzzer)
```

## Backlog inicial

| Tarefa | Responsável | Status |
|---|---|---|
| Criar repositório | Daniela | Feito |
| Preencher README inicial | Daniela e João | Feito |
| Testar ESP32 com LED | Marcelo | A fazer |
| Identificar sensor principal (DHT22 e LDR) | Willian | A fazer |
| Testar sensor de umidade do solo | Willian | A fazer |
| Listar componentes necessários | Leonardo | Incompleto |
| Desenhar arquitetura inicial | Ana | A fazer |
| Registrar primeiro risco técnico | Daniela | Feito |

## Primeiro risco técnico

A rede Wi-Fi da instituição, usada na sala de aula, pode exigir portal cativo ou autenticação que o ESP32 não suporta nativamente, impedindo a conexão direta ao broker MQTT. Isso precisa ser investigado (rede aberta disponível, liberação de IP, ou uso de rede alternativa para os testes).
