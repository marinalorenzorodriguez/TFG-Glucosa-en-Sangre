#include <Wire.h>
#include "MAX30105.h"
#include "heartRate.h"
#include <SigFox.h>

#define N_MUESTRAS 10   

// Umbrales de alerta
#define GLUCOSA_HIPO 70.0      // mg/dL
#define GLUCOSA_HIPER 180.0    // mg/dL
#define FC_BAJA 60             // bpm
#define FC_ALTA 100            // bpm
#define SPO2_BAJO 95           // %

MAX30105 particleSensor;

// ############# Variables glucosa #############
float glucosaHist[100];
int sampleCount = 0;

// ############## Variables frecuencia cardiaca #######
long lastBeat = -1;
float bpmSum = 0;
int bpmCount = 0;
int bpmFinal = 0;

// ############## Variables SpO2 ###########################
float spo2Sum = 0;
int spo2Count = 0;
int spo2Final = 0;


// ###############Comienzo del bucle #######################
void setup() {
  Serial.begin(115200);
  delay(2000);

  if (!SigFox.begin()) {
    Serial.println("Error Sigfox");
    while (1);
  }

  if (!particleSensor.begin(Wire, I2C_SPEED_STANDARD)) {
    Serial.println("No se detecta MAX30102");
    while (1);
  }

  particleSensor.setup();
  Serial.println("Sistema iniciado");
}

void loop() {
  float beatsPerMinute = 0;

  // ###########Cálculo de bpm y oxígeno##########

  bpmSum = 0;
  bpmCount = 0;
  spo2Sum = 0;
  spo2Count = 0;
  lastBeat = -1;

  particleSensor.setPulseAmplitudeRed(0x1F);
  particleSensor.setPulseAmplitudeIR(0x1F);

  unsigned long startTime = millis();
  while (millis() - startTime < 15000) {
    long irValue = particleSensor.getIR();
    long redValue = particleSensor.getRed();

    if (checkForBeat(irValue)) {
      long now = millis();
      if (lastBeat > 0) {
        long delta = now - lastBeat;
        beatsPerMinute = 60.0 / (delta / 1000.0);
        if (beatsPerMinute > 30 && beatsPerMinute < 160) {
          bpmSum += beatsPerMinute;
          bpmCount++;
        }
      }
      lastBeat = now;
    }

    if (irValue > 3000 && redValue > 3000) {
      float ratio = (float)redValue / (float)irValue;
      float spo2 = 110.0 - 25.0 * ratio;

      if (spo2 > 70 && spo2 <= 100) {
        spo2Sum += spo2;
        spo2Count++;
      }
    }
  }

  bpmFinal = (bpmCount > 0) ? bpmSum / bpmCount : 0;
  spo2Final = (spo2Count > 0) ? spo2Sum / spo2Count : 0;

  //##########Cálculo de glucosa###########################

  long irSum = 0;
  for (int i = 0; i < 5; i++) {
    irSum += particleSensor.getIR();
    delay(200);
  }

  float irValue = irSum / 5.0;
  float irVoltage = (irValue / 262144.0) * 1.8;
  float glucose = 0.97 * irVoltage * 100 + 63.41;

  glucosaHist[sampleCount++] = glucose;

  // Evitar overflow del array
  if (sampleCount >= 100) {
    sampleCount = N_MUESTRAS;
  }

  // #########Resumen de N=10 muestras para imprimir en consola y procesamiento final##########
 
  if (sampleCount >= N_MUESTRAS && sampleCount % N_MUESTRAS == 0) {
    float suma = 0;
    for (int i = sampleCount - N_MUESTRAS; i < sampleCount; i++) {
      suma += glucosaHist[i];
    }
    float media = suma / N_MUESTRAS;

    float maxDesviacion = 0;
    for (int i = sampleCount - N_MUESTRAS; i < sampleCount; i++) {
      float diff = glucosaHist[i] - media;
      if (abs(diff) > abs(maxDesviacion)) {
        maxDesviacion = diff;
      }
    }

    // ####calcular flags#################################################
    uint8_t flags = 0;

    // Inicializar flags temporales
    bool signo_flag = false;
    bool fc_alta_flag = false;
    bool fc_baja_flag = false;
    bool oxigeno_bajo_flag = false;

    // Revisar las últimas N_MUESTRAS para eventos puntuales
    for (int i = sampleCount - N_MUESTRAS; i < sampleCount; i++) {
      if (bpmFinal > FC_ALTA) fc_alta_flag = true;
      if (bpmFinal < FC_BAJA && bpmFinal > 0) fc_baja_flag = true;
      if (spo2Final < SPO2_BAJO && spo2Final > 0) oxigeno_bajo_flag = true;
    }

    // Bit 0: Glucosa sube/baja según desviación máxima
    if (maxDesviacion > 0) signo = true;

    // Asignar bits
    if (signo_flag) flags |= (1 << 0);
    if (fc_alta_flag) flags |= (1 << 1);
    if (fc_baja_flag) flags |= (1 << 2);
    if (oxigeno_bajo_flag) flags |= (1 << 3);
    if (bpmFinal == 0 || spo2Final == 0) flags |= (1 << 4);

    // ####################Imprimir resumen por pantalla#############################
    Serial.println("\n--- RESUMEN ---");
    Serial.print("Glucosa media: "); Serial.print(media, 2); Serial.println(" mg/dL");
    Serial.print("Variación máx: "); Serial.print(abs(maxDesviacion), 2); Serial.println(" mg/dL");
    Serial.print("BPM medio: "); Serial.println(bpmFinal);
    Serial.print("SpO2 medio: "); Serial.print(spo2Final); Serial.println(" %");

    // ################Payload Sigfox#####################################
    // Bytes 0-1: Glucosa media (uint16) x100
    uint16_t glucosa_uint16 = (uint16_t)(media * 100);
    // Bytes 2-3: Variación media (uint16) x100
    uint16_t variacion_uint16 = (uint16_t)(abs(maxDesviacion) * 100);
    // Bytes 4-5: Frecuencia cardíaca (uint16)
    uint16_t bpm_uint16 = (uint16_t)bpmFinal;
    // Byte 6: Oxígeno (uint8)
    uint8_t oxigeno_uint8 = (uint8_t)spo2Final;

    // Empaquetar en formato big-endian
    byte payload[8] = {
      highByte(glucosa_uint16),      // Byte 0
      lowByte(glucosa_uint16),       // Byte 1
      highByte(variacion_uint16),    // Byte 2
      lowByte(variacion_uint16),     // Byte 3
      highByte(bpm_uint16),          // Byte 4
      lowByte(bpm_uint16),           // Byte 5
      oxigeno_uint8,                 // Byte 6
      flags                          // Byte 7
    };

    // Mostrar payload en hexadecimal
    Serial.print("\n📡 Payload Sigfox (hex): ");
    for (int i = 0; i < 8; i++) {
      if (payload[i] < 0x10) Serial.print("0");
      Serial.print(payload[i], HEX);
    }
    Serial.println();


    // Enviar por Sigfox
    SigFox.beginPacket();
    SigFox.write(payload, 8);
    int result = SigFox.endPacket();

    if (result == 0) {
      Serial.println("✓ Mensaje Sigfox enviado correctamente");
    } else {
      Serial.print("✗ Error enviando Sigfox: ");
      Serial.println(result);
    }
  }

  particleSensor.setPulseAmplitudeRed(0x00);
  particleSensor.setPulseAmplitudeIR(0x00);

  delay(600000);
}
