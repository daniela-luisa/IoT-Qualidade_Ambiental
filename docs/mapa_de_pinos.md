# Mapa de pinos (por nó: estacao-01 e estacao-02, idêntico)

Placa: ESP32 DevKit. Esquema completo em [`Esquema_Eletrico_Por_No.jpeg`](Esquema_Eletrico_Por_No.jpeg); lista de materiais e consumo em [`BOM.md`](BOM.md).

| GPIO | Função | Tipo | Observação |
|------|--------|------|------------|
| 14 | DATA do DHT11 | entrada digital | pull-up de 10 kΩ ao 3V3 se o módulo não tiver |
| 35 | AO do módulo de solo | entrada analógica (ADC1_CH7) | só entrada; sensor em 3V3 |
| 34 | AO do módulo LDR | entrada analógica (ADC1_CH6) | só entrada; sensor em 3V3 |
| 26 | LED | saída digital | resistor de 330 Ω (≈ 3,9 mA) |
| 25 | Buzzer passivo | saída (tone) | via transistor NPN BC337 e resistor de 1 kΩ na base |
| 27 | IN do módulo relé (bomba) | saída open-drain | relé ativo em BAIXO: pino solto (entrada) = desligado, GND = ligado. O pino nunca vai a HIGH (com módulo de 5 V o HIGH de 3,3 V não soltava o relé). Fica solto desde o boot (estado seguro) |
| 0 | Botão BOOT (já soldado na placa) | entrada, pull-up interno | apertado = BAIXO: silencia o alarme. Não ligar nada externo |
| 3V3 | VCC de DHT11, solo e LDR | alimentação | ≈ 13 mA no total |
| VIN (5 V) | VCC do relé e buzzer | alimentação | relé ≈ 72 mA, buzzer ≈ 30 mA |
| GND | trilho único | | comum a ESP32, sensores, relé e fonte da bomba |

Conferência: nenhum GPIO em 6 a 11 nem nos pinos de strapping (2, 5, 12, 15) — o 0 é só lido, pelo botão BOOT que a placa já tem; sensores analógicos no ADC1;
nenhuma carga ligada direto ao GPIO; bomba com fonte própria de 5 V / 2 A e diodo 1N4007 em antiparalelo.
