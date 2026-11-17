# Mapa de pinos (por nó: estacao-01 e estacao-02, idêntico)

Placa: ESP32 DevKit. Esquema completo em `docs/esquema_eletrico.png`.

| GPIO | Função | Tipo | Observação |
|------|--------|------|------------|
| 14 | DATA do DHT11 | entrada digital | pull-up de 10 kΩ ao 3V3 se o módulo não tiver |
| 35 | AO do módulo de solo | entrada analógica (ADC1_CH7) | só entrada; sensor em 3V3 |
| 34 | AO do módulo LDR | entrada analógica (ADC1_CH6) | só entrada; sensor em 3V3 |
| 26 | LED | saída digital | resistor de 330 Ω (≈ 3,9 mA) |
| 25 | Buzzer passivo | saída (tone) | via transistor NPN BC337 e resistor de 1 kΩ na base |
| 27 | IN do módulo relé (bomba) | saída digital | nível alto = relé desligado (estado seguro no boot) |
| 3V3 | VCC de DHT11, solo e LDR | alimentação | ≈ 13 mA no total |
| VIN (5 V) | VCC do relé e buzzer | alimentação | relé ≈ 72 mA, buzzer ≈ 30 mA |
| GND | trilho único | | comum a ESP32, sensores, relé e fonte da bomba |

Conferência: nenhum GPIO em 6 a 11 nem nos pinos de strapping (0, 2, 5, 12, 15); sensores analógicos no ADC1;
nenhuma carga ligada direto ao GPIO; bomba com fonte própria de 5 V / 2 A e diodo 1N4007 em antiparalelo.
