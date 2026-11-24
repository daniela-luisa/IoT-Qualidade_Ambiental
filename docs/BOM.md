# Lista de materiais (BOM) e alimentação

Por nó (`estacao-01` e `estacao-02`). A estação **sem bomba** (`TEM_BOMBA 0` no firmware) dispensa os itens
marcados com **(B)**. Ligações e pinos em [`mapa_de_pinos.md`](mapa_de_pinos.md); esquema em
[`Esquema_Eletrico_Por_No.jpeg`](Esquema_Eletrico_Por_No.jpeg).

## Componentes

| # | Item | Qtd por nó | Função | Observação |
|---|------|-----------|--------|------------|
| 1 | ESP32 DevKit (ESP32-D0WD, Wi-Fi) | 1 | microcontrolador | botão BOOT da placa usado para silenciar o alarme (GPIO 0) |
| 2 | Módulo DHT11 | 1 | temperatura e umidade do ar | GPIO 14; pull-up de 10 kΩ se o sensor vier sem placa |
| 3 | Sensor de umidade do solo capacitivo (saída AO) | 1 | umidade do solo | GPIO 35 (ADC1), alimentado em 3V3 |
| 4 | Módulo LDR com saída AO | 1 | luminosidade | GPIO 34 (ADC1), alimentado em 3V3 |
| 5 | LED 5 mm | 1 | alerta visual (apagado, aceso ou piscando) | GPIO 26 |
| 6 | Resistor 330 Ω | 1 | limita a corrente do LED | ≈ 3,9 mA |
| 7 | Buzzer passivo | 1 | alerta sonoro | GPIO 25, `tone()` 1 kHz |
| 8 | Transistor NPN BC337 + resistor 1 kΩ (base) | 1 + 1 | aciona o buzzer | a carga não sai direto do GPIO |
| 9 | Módulo relé 1 canal 5 V **(B)** | 1 | liga a bomba | IN no GPIO 27 (open-drain: solto = desligado) |
| 10 | Mini bomba submersível 5 V **(B)** | 1 | irrigação | alimentada pela fonte própria, nunca pelo ESP32 |
| 11 | Diodo 1N4007 **(B)** | 1 | proteção contra o pico da bomba | em antiparalelo com a bomba (faixa no +) |
| 12 | Fonte 5 V / 2 A **(B)** | 1 | alimenta a bomba | GND comum com o ESP32 |
| 13 | Mangueira de silicone + reservatório de água **(B)** | 1 | leva a água até o vaso | |
| 14 | Protoboard 830 pontos | 1 | montagem | |
| 15 | Jumpers macho-macho e macho-fêmea | ~20 | ligações | |
| 16 | Cabo USB (dados) | 1 | alimentação do ESP32 e gravação | também serve um power bank 5 V |
| 17 | Caixa/suporte | 1 | acabamento e fixação ao vaso | impressão 3D, acrílico ou caixa montada |

## Alimentação (dimensionamento)

| Ramo | Fonte | Consumo | Observação |
|------|-------|---------|------------|
| ESP32 com Wi-Fi | USB 5 V (computador ou power bank) | picos de ~240 mA na transmissão Wi-Fi | valor típico de datasheet do ESP32 |
| DHT11 + solo + LDR | 3V3 do ESP32 | ≈ 13 mA no total | dentro da folga do regulador da placa |
| LED | GPIO 26 | ≈ 3,9 mA | resistor de 330 Ω |
| Relé **(B)** | conforme o módulo (ver nota) | ≈ 72 mA na bobina | o GPIO só aciona o opto, não alimenta a bobina |
| Buzzer | 5 V via BC337 | ≈ 30 mA | o GPIO só aciona a base (1 kΩ) |
| Bomba **(B)** | fonte própria 5 V / 2 A | **medir com multímetro**; mini bombas de 5 V costumam ficar entre 100 e 300 mA | a fonte de 2 A cobre a partida com folga |

Regras seguidas: nenhuma carga ligada direto num GPIO; a bomba não usa o 5 V do ESP32; GND comum entre ESP32,
sensores, relé e fonte da bomba; diodo de proteção na bomba; relé em estado desligado desde o boot (o firmware
deixa o pino solto antes de qualquer outra coisa).

> **Nota (relé):** o cabeçalho do firmware descreve o VCC do relé no 3V3 do ESP32, e o mapa de pinos descreve
> no VIN (5 V). Confira na montagem real e deixe os dois documentos iguais antes da defesa.
