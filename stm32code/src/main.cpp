#include <Arduino.h>
#include <Wire.h>
#include <SEGGER_RTT.h>

extern "C" int _write(int file, char *ptr, int len) {
  SEGGER_RTT_Write(0, ptr, len);
  return len;
}

// ==================== РАСПИНОВКА ПО СХЕМЕ KICAD ====================
// Моторы (4WD Mecanum)
#define PIN_MOT_EN     PB0
#define PIN_STEP_X     PA0  // Задний левый (M1_X)
#define PIN_DIR_X      PA4
#define PIN_STEP_Y     PA1  // Передний левый (M2_Y)
#define PIN_DIR_Y      PA5
#define PIN_STEP_Z     PA2  // Задний правый (M3_Z)
#define PIN_DIR_Z      PA6
#define PIN_STEP_A     PA3  // Передний правый (M4_A)
#define PIN_DIR_A      PA7

// Ультразвуковые датчики (HC-SR04 / US-100)
#define PIN_US_FRONT_TRIG PB12
#define PIN_US_FRONT_ECHO PB13
#define PIN_US_LEFT_TRIG  PB14
#define PIN_US_LEFT_ECHO  PB15
#define PIN_US_RIGHT_TRIG PA8
#define PIN_US_RIGHT_ECHO PA9

// ИК-датчики препятствий (активный LOW)
#define PIN_IR_FRONT   PB3
#define PIN_IR_LEFT    PB4
#define PIN_IR_RIGHT   PB5
#define PIN_IR_REAR    PA10

// Светодиод индикации на плате Black Pill
#define PIN_LED        PC13

// Адрес MPU-6050
#define MPU6050_ADDR   0x68

// ==================== СТРУКТУРЫ ПРОТОКОЛА ====================
// Входящий пакет от Python (7 байт: 0xBB, cmd, int16 L, int16 R, crc8)
struct __attribute__((packed)) CmdPacket {
  uint8_t header;
  uint8_t cmd_type;
  int16_t left_speed;
  int16_t right_speed;
  uint8_t crc;
};

// Исходящий пакет телеметрии для Python (ровно 56 байт: <B7h3HB4i4H4HBB)[cite: 16]
struct __attribute__((packed)) TelemPacket {
  uint8_t header;        // 0xAA[cite: 16]
  int16_t accel[3];      // X, Y, Z[cite: 16]
  int16_t gyro[3];       // X, Y, Z[cite: 16]
  int16_t temp;          // Сырая температура MPU-6050[cite: 16]
  uint16_t us[3];        // front_mm, left_mm, right_mm[cite: 16]
  uint8_t ir_mask;       // Биты: 0-Front, 1-Left, 2-Right, 3-Rear[cite: 16]
  int32_t steps[4];      // Счётчики шагов M1..M4[cite: 16]
  uint16_t current[4];   // Ток моторов (мА)[cite: 16]
  uint16_t vibro[4];     // Вибрация / StallGuard[cite: 16]
  uint8_t drv_flags;     // Флаги состояния драйверов (0 = OK)[cite: 16]
  uint8_t crc;           // CRC8 (полином 0x07)[cite: 16]
};

// ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
TelemPacket telem;
bool imu_ok = false;

// Управление шаговиками (генерация импульсов без блокировки)
volatile int16_t target_speed[4] = {0, 0, 0, 0}; // X(L), Y(L), Z(R), A(R)
uint32_t last_step_us[4] = {0, 0, 0, 0};
bool step_pin_state[4] = {false, false, false, false};

const uint8_t STEP_PINS[4] = {PIN_STEP_X, PIN_STEP_Y, PIN_STEP_Z, PIN_STEP_A};
const uint8_t DIR_PINS[4]  = {PIN_DIR_X,  PIN_DIR_Y,  PIN_DIR_Z,  PIN_DIR_A};
// Инверсия направления при необходимости (1 или -1)
const int8_t DIR_INV[4]    = {1, 1, -1, -1};

// Таймеры опроса датчиков
uint32_t last_sensor_ms = 0;
uint8_t us_channel = 0;
uint32_t last_led_ms = 0;

// Буфер приёма RTT
uint8_t rx_buf[16];
uint8_t rx_idx = 0;

// ==================== CRC8 (полином 0x07, init 0x00) ====================
uint8_t calc_crc8(const uint8_t *data, size_t len) {
  uint8_t crc = 0x00;
  for (size_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t b = 0; b < 8; b++) {
      if (crc & 0x80) {
        crc = (crc << 1) ^ 0x07;
      } else {
        crc <<= 1;
      }
    }
  }
  return crc;
}

// ==================== MPU-6050 ====================
void init_mpu6050() {
  Wire.begin();
  Wire.setClock(400000);
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(0x6B); // PWR_MGMT_1
  Wire.write(0x00); // Выход из сна
  if (Wire.endTransmission() == 0) {
    imu_ok = true;
  } else {
    imu_ok = false;
  }
}

void read_mpu6050() {
  if (!imu_ok) {
    telem.accel[0] = telem.accel[1] = telem.accel[2] = 0;
    telem.gyro[0] = telem.gyro[1] = telem.gyro[2] = 0;
    telem.temp = 0;
    return;
  }

  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(0x3B); // ACCEL_XOUT_H
  if (Wire.endTransmission(false) != 0) {
    imu_ok = false;
    return;
  }

  if (Wire.requestFrom((uint8_t)MPU6050_ADDR, (uint8_t)14) == 14) {
    telem.accel[0] = (Wire.read() << 8) | Wire.read();
    telem.accel[1] = (Wire.read() << 8) | Wire.read();
    telem.accel[2] = (Wire.read() << 8) | Wire.read();
    telem.temp     = (Wire.read() << 8) | Wire.read();
    telem.gyro[0]  = (Wire.read() << 8) | Wire.read();
    telem.gyro[1]  = (Wire.read() << 8) | Wire.read();
    telem.gyro[2]  = (Wire.read() << 8) | Wire.read();
  }
}

// ==================== УЛЬТРАЗВУК И ИК ====================
uint16_t read_ultrasonic_mm(uint8_t trig, uint8_t echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);

  // Таймаут 15 мс (~2.5 метра), чтобы не тормозить шаговики
  uint32_t dur = pulseIn(echo, HIGH, 15000);
  if (dur == 0) {
    return 2500; // Нет препятствия в пределах 2.5 м
  }
  return (uint16_t)((dur * 343UL) / 2000UL);
}

void update_ir_sensors() {
  uint8_t mask = 0;
  if (digitalRead(PIN_IR_FRONT) == LOW) mask |= 0x01; //[cite: 16]
  if (digitalRead(PIN_IR_LEFT)  == LOW) mask |= 0x02; //[cite: 16]
  if (digitalRead(PIN_IR_RIGHT) == LOW) mask |= 0x04; //[cite: 16]
  if (digitalRead(PIN_IR_REAR)  == LOW) mask |= 0x08; //[cite: 16]
  telem.ir_mask = mask;
}

// ==================== МОТОРЫ ====================
void set_motors(int16_t left_spd, int16_t right_spd) {
  target_speed[0] = left_spd;  // M1_X (задний левый)[cite: 1, 16]
  target_speed[1] = left_spd;  // M2_Y (передний левый)[cite: 1, 16]
  target_speed[2] = right_spd; // M3_Z (задний правый)[cite: 1, 16]
  target_speed[3] = right_spd; // M4_A (передний правый)[cite: 1, 16]

  bool any_moving = (left_spd != 0 || right_spd != 0);
  digitalWrite(PIN_MOT_EN, any_moving ? LOW : HIGH); // LOW = драйверы включены
}

void run_steppers() {
  uint32_t now_us = micros();
  for (int i = 0; i < 4; i++) {
    int16_t spd = target_speed[i] * DIR_INV[i];
    if (spd == 0) continue;

    digitalWrite(DIR_PINS[i], (spd > 0) ? HIGH : LOW);
    uint32_t half_period_us = 500000UL / (uint32_t)abs(spd);

    if (now_us - last_step_us[i] >= half_period_us) {
      last_step_us[i] = now_us;
      step_pin_state[i] = !step_pin_state[i];
      digitalWrite(STEP_PINS[i], step_pin_state[i] ? HIGH : LOW);

      if (step_pin_state[i]) {
        telem.steps[i] += (target_speed[i] > 0) ? 1 : -1;
      }
    }
  }
}

void run_driver_self_test() {
  // Короткий тестовый импульс для команды TEST_DRV (cmd_type == 5)[cite: 17]
  digitalWrite(PIN_MOT_EN, LOW);
  for (int i = 0; i < 4; i++) {
    digitalWrite(DIR_PINS[i], HIGH);
    for (int s = 0; s < 20; s++) {
      digitalWrite(STEP_PINS[i], HIGH);
      delayMicroseconds(500);
      digitalWrite(STEP_PINS[i], LOW);
      delayMicroseconds(500);
      telem.steps[i]++;
    }
  }
  set_motors(0, 0);
  telem.drv_flags = 0;
}

// ==================== ОБРАБОТКА ПАКЕТОВ RTT ====================
void send_telemetry() {
  telem.header = 0xAA; //[cite: 16]
  telem.crc = calc_crc8((const uint8_t *)&telem, sizeof(TelemPacket) - 1); //[cite: 16]
  SEGGER_RTT_Write(0, &telem, sizeof(TelemPacket));
}

void process_rtt_commands() {
  while (SEGGER_RTT_HasData(0)) {
    uint8_t byte_in;
    if (SEGGER_RTT_Read(0, &byte_in, 1) == 1) {
      // Ждём стартовый байт 0xBB[cite: 16]
      if (rx_idx == 0 && byte_in != 0xBB) {
        continue;
      }
      rx_buf[rx_idx++] = byte_in;

      if (rx_idx == sizeof(CmdPacket)) {
        rx_idx = 0;
        CmdPacket *cmd = (CmdPacket *)rx_buf;

        if (calc_crc8(rx_buf, sizeof(CmdPacket) - 1) == cmd->crc) { //[cite: 16]
          switch (cmd->cmd_type) {
            case 1: // DRIVE
              set_motors(cmd->left_speed, cmd->right_speed);
              break;
            case 2: // STOP[cite: 16, 17]
              set_motors(0, 0);
              break;
            case 4: // POLL (только запрос телеметрии)[cite: 16]
              break;
            case 5: // TEST_DRV[cite: 16, 17]
              run_driver_self_test();
              break;
            default:
              break;
          }
          // Мгновенно отвечаем 56-байтным пакетом Python-скрипту[cite: 16]
          send_telemetry();
        }
      }
    }
  }
}

// ==================== SETUP & LOOP ====================
void setup() {
  SEGGER_RTT_Init();

  pinMode(PIN_LED, OUTPUT);
  digitalWrite(PIN_LED, HIGH);

  // Пины моторов[cite: 1]
  pinMode(PIN_MOT_EN, OUTPUT);
  digitalWrite(PIN_MOT_EN, HIGH);
  for (int i = 0; i < 4; i++) {
    pinMode(STEP_PINS[i], OUTPUT);
    pinMode(DIR_PINS[i], OUTPUT);
    digitalWrite(STEP_PINS[i], LOW);
    digitalWrite(DIR_PINS[i], LOW);
  }

  // Пины сонаров
  pinMode(PIN_US_FRONT_TRIG, OUTPUT);
  pinMode(PIN_US_FRONT_ECHO, INPUT);
  pinMode(PIN_US_LEFT_TRIG,  OUTPUT);
  pinMode(PIN_US_LEFT_ECHO,  INPUT);
  pinMode(PIN_US_RIGHT_TRIG, OUTPUT);
  pinMode(PIN_US_RIGHT_ECHO, INPUT);

  // Пины ИК-датчиков
  pinMode(PIN_IR_FRONT, INPUT_PULLUP);
  pinMode(PIN_IR_LEFT,  INPUT_PULLUP);
  pinMode(PIN_IR_RIGHT, INPUT_PULLUP);
  pinMode(PIN_IR_REAR,  INPUT_PULLUP);

  memset(&telem, 0, sizeof(TelemPacket));
  telem.us[0] = 2000;
  telem.us[1] = 2000;
  telem.us[2] = 2000;

  init_mpu6050();
}

void loop() {
  // 1. Приоритет: генерация шагов и приём команд из RTT
  run_steppers();
  process_rtt_commands();

  uint32_t now_ms = millis();

  // 2. Поочерёдный опрос датчиков каждые 30 мс (чтобы не тормозить шаговики)
  if (now_ms - last_sensor_ms >= 30) {
    last_sensor_ms = now_ms;
    update_ir_sensors();

    if (us_channel == 0) {
      telem.us[0] = read_ultrasonic_mm(PIN_US_FRONT_TRIG, PIN_US_FRONT_ECHO);
    } else if (us_channel == 1) {
      telem.us[1] = read_ultrasonic_mm(PIN_US_LEFT_TRIG, PIN_US_LEFT_ECHO);
    } else if (us_channel == 2) {
      telem.us[2] = read_ultrasonic_mm(PIN_US_RIGHT_TRIG, PIN_US_RIGHT_ECHO);
    } else {
      read_mpu6050();
    }
    us_channel = (us_channel + 1) & 0x03;
  }

  // 3. Индикация работы (мигание PC13)
  if (now_ms - last_led_ms >= 500) {
    last_led_ms = now_ms;
    digitalWrite(PIN_LED, !digitalRead(PIN_LED));
  }
}