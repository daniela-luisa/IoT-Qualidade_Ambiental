# Projeto IoT - Grupo 8 - Qualidade Ambiental

## Integrantes

- Ana Beatriz Pedrozo
- Daniela Luisa da Conceição
- Marcelo Gustavo Eger
- João Henrique Souza Rocha
- Leonardo André Ferreira
- Willian Squena

## Família temática

Grupo 8 - Qualidade Ambiental.

Construção de uma pequena estação ambiental conectada, que mede variáveis do ambiente, classifica a condição do local e sinaliza a condição por LED e buzzer.

## Problema

Salas de aula podem apresentar condições ambientais inadequadas (temperatura elevada, umidade fora da faixa de conforto ou pouca luminosidade) sem que ninguém perceba em tempo real, o que pode prejudicar o conforto e a concentração de quem está no espaço.

## Usuário ou contexto de uso

Professores, alunos e responsáveis pela gestão da sala de aula, que precisam saber rapidamente se as condições do ambiente estão adequadas.

## Objetivo do Projeto

Construir um dispositivo com ESP32 que:

- leia pelo menos duas variáveis ambientais (temperatura, umidade e luminosidade);
- classifique a condição do ambiente
- publique telemetria via MQTT;
- receba comando remoto;
- acione um alerta físico (LED e buzzer) e confirme a ação executada.

## Componentes previstos

- ESP32
- Sensor de temperatura e umidade (DHT22 ou DHT11)
- Sensor de luminosidade (LDR)
- LED RGB
- Buzzer

## Arquitetura inicial

```
Sensor -> ESP32 -> Wi-Fi -> MQTT -> comando -> atuador (LED + buzzer)
```

## Backlog inicial

| Tarefa | Responsável | Status |
|---|---|---|
| Criar repositório | Daniela | Feito |
| Preencher README inicial  |Daniela e João | Feito |
| Testar ESP32 com LED | Marcelo | A fazer |
| Identificar sensor principal (DHT22/DHT11 e LDR) | Willian | A fazer |
| Listar componentes necessários | Leonardo | Incompleto |
| Desenhar arquitetura inicial | Ana | A fazer |
| Registrar primeiro risco técnico | Daniela | Feito |
    
## Primeiro risco técnico

A rede Wi-Fi da instituição, usada na sala de aula, pode exigir portal cativo ou autenticação que o ESP32 não suporta nativamente, impedindo a conexão direta ao broker MQTT. Isso precisa ser investigado (rede aberta disponível, liberação de IP, ou uso de rede alternativa para os testes).

