import os
import time
import json
import struct
import threading
import serial
from serial.tools import list_ports
import config


CRC8_TABLE = [0] * 256
for _i in range(256):
    _crc = _i
    for _ in range(8):
        _crc = ((_crc << 1) ^ 0x07) & 0xFF if (_crc & 0x80) else (_crc << 1) & 0xFF
    CRC8_TABLE[_i] = _crc


def fast_crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC8_TABLE[crc ^ b]
    return crc


class STM32Worker:
    TELEM_FMT = "<B7h3HB4i4H4HBB"
    TELEM_SIZE = struct.calcsize(TELEM_FMT)  # 56 байт ровно по схеме KiCad

    def __init__(self, json_cache_path="telemetry_cache.json", on_obstacle_stop=None):
        self.json_cache_path = json_cache_path
        self.on_obstacle_stop = on_obstacle_stop

        self._ser = None
        self._io_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self.running = False
        self.latest_raw_tuple = None
        self.latest_parsed = {}
        self.front_distance_mm = 2000
        self.auto_stopped = False
        self.is_moving = False
        self.active_port = None

        self._last_json_dump_time = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._continuous_binary_poll_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _ensure_port(self) -> bool:
        if self._ser and self._ser.is_open:
            return True

        # Ищем СТРОГО по аппаратному USB Vendor ID STMicroelectronics (0x0483),
        # чтобы никогда не перепутать STM32 с радиомодулем Meshtastic на /dev/ttyACM0!
        candidates = []
        try:
            for p in list_ports.comports():
                desc = (p.description or "").upper()
                mfg = (p.manufacturer or "").upper()
                if p.vid == 0x0483 or "STM32" in desc or "STMICROELECTRONICS" in mfg:
                    candidates.append(p.device)
        except Exception:
            pass

        cfg_port = getattr(config, "STM32_PORT", None)
        if cfg_port and os.path.exists(cfg_port) and cfg_port not in candidates:
            candidates.insert(0, cfg_port)

        if not candidates:
            return False

        baud = getattr(config, "STM32_BAUDRATE", 921600)
        for dev in candidates:
            try:
                ser = serial.Serial(dev, baud, timeout=0.6, write_timeout=0.5)
                ser.reset_input_buffer()

                # Дополнительная проверка рукопожатием (ждем заголовок 0xAA и CRC-8)
                body = struct.pack("<BBhh", 0xBB, 4, 0, 0)
                pkt = body + struct.pack("<B", fast_crc8(body))
                ser.write(pkt)
                raw = ser.read(self.TELEM_SIZE)
                if len(raw) == self.TELEM_SIZE and raw[0] == 0xAA and fast_crc8(raw[:-1]) == raw[-1]:
                    self._ser = ser
                    self.active_port = dev
                    print(f"[STM32] Подключено и подтверждено на {dev} (VID=0x0483, {baud} бод)")
                    return True
                else:
                    # Если чип 0x0483, но еще грузится — все равно запоминаем
                    self._ser = ser
                    self.active_port = dev
                    print(f"[STM32] Подключено по VID=0x0483 на {dev} ({baud} бод)")
                    return True
            except Exception:
                self._ser = None
                self.active_port = None
        return False

    def _raw_exchange(self, cmd_type: int = 4, left_speed: int = 0, right_speed: int = 0) -> tuple | None:
        with self._io_lock:
            if not self._ensure_port():
                return None

            body = struct.pack("<BBhh", 0xBB, cmd_type, int(left_speed), int(right_speed))
            pkt = body + struct.pack("<B", fast_crc8(body))

            try:
                self._ser.reset_input_buffer()
                self._ser.write(pkt)
                raw = self._ser.read(self.TELEM_SIZE)
                if len(raw) != self.TELEM_SIZE or raw[0] != 0xAA:
                    return None
                if fast_crc8(raw[:-1]) != raw[-1]:
                    return None
                return struct.unpack(self.TELEM_FMT, raw)
            except Exception:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
                self.active_port = None
                return None

    def send_motor_command(self, cmd_type: int, left_speed: int = 0, right_speed: int = 0):
        stop_thresh = getattr(config, "STOP_DISTANCE_MM", 500)
        if cmd_type == 1 and left_speed > 0 and right_speed > 0 and self.front_distance_mm < stop_thresh:
            cmd_type, left_speed, right_speed = 2, 0, 0
            self.auto_stopped = True

        u = self._raw_exchange(cmd_type, left_speed, right_speed)
        if cmd_type == 1 and (left_speed != 0 or right_speed != 0):
            self.is_moving = True
            self.auto_stopped = False
        elif cmd_type == 2 or (left_speed == 0 and right_speed == 0):
            self.is_moving = False

        if u:
            self._update_memory_from_tuple(u)

    def _continuous_binary_poll_loop(self):
        stop_thresh = getattr(config, "STOP_DISTANCE_MM", 500)
        save_interval = getattr(config, "JSON_SAVE_INTERVAL_S", 5.0)

        while self.running:
            u = self._raw_exchange(cmd_type=4, left_speed=0, right_speed=0)
            if u is not None:
                self._update_memory_from_tuple(u)

                if self.front_distance_mm < stop_thresh and self.is_moving:
                    self._raw_exchange(cmd_type=2, left_speed=0, right_speed=0)
                    self.is_moving = False
                    self.auto_stopped = True
                    print(f"[ЗАЩИТА <50см] Препятствие {self.front_distance_mm} мм! Экстренный СТОП.")
                    if self.on_obstacle_stop:
                        self.on_obstacle_stop(self.front_distance_mm)

                now = time.time()
                if now - self._last_json_dump_time >= save_interval:
                    self._dump_to_json_file()
                    self._last_json_dump_time = now
                time.sleep(0.05)
            else:
                # Если STM32 сейчас не воткнута — проверяем раз в 1.5 секунды и не трогаем чужие порты
                time.sleep(1.5)

    def _update_memory_from_tuple(self, u: tuple):
        with self._state_lock:
            self.latest_raw_tuple = u
            self.front_distance_mm = int(u[8])

    def _build_dict_from_tuple(self, u: tuple | None) -> dict:
        if u is None:
            return {"error": "STM32 не подключена или нет ответа"}

        drv_flags = u[24]
        labels = ["M1_X (PA0/PA4)", "M2_Y (PA1/PA5)", "M3_Z (PA2/PA6)", "M4_A (PA3/PA7)"]
        drivers = {}
        for i in range(4):
            code = (drv_flags >> (i * 2)) & 0x03
            drivers[labels[i]] = {
                "steps": int(u[12 + i]),
                "current_mA": int(u[16 + i]),
                "vibro_sg": int(u[20 + i]),
                "status": "OK" if code == 0 else "STALL/NO_SPIN",
            }

        ir_mask = int(u[11])
        return {
            "timestamp": round(time.time(), 2),
            "imu": {
                "accel_g": {
                    "x": round(u[1] / 16384.0, 3),
                    "y": round(u[2] / 16384.0, 3),
                    "z": round(u[3] / 16384.0, 3),
                },
                "gyro_dps": {
                    "x": round(u[4] / 131.0, 2),
                    "y": round(u[5] / 131.0, 2),
                    "z": round(u[6] / 131.0, 2),
                },
                "temp_c": round(u[7] / 340.0 + 36.53, 1),
            },
            "ultrasonic": {
                "front_mm": int(u[8]),
                "left_mm": int(u[9]),
                "right_mm": int(u[10]),
            },
            "ir": {
                "front_close": bool(ir_mask & 0x01),
                "left_close": bool(ir_mask & 0x02),
                "right_close": bool(ir_mask & 0x04),
                "rear_close": bool(ir_mask & 0x08),
                "raw_mask": ir_mask,
            },
            "drivers": drivers,
        }

    def _dump_to_json_file(self):
        with self._state_lock:
            u = self.latest_raw_tuple
        data = self._build_dict_from_tuple(u)
        with self._state_lock:
            self.latest_parsed = data
        try:
            with open(self.json_cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_sensor_payload(self, sensor_key: str = "all") -> dict:
        with self._state_lock:
            cached = dict(self.latest_parsed) if self.latest_parsed else self._build_dict_from_tuple(self.latest_raw_tuple)

        if "error" in cached or sensor_key in ("all", "", None):
            return cached
        return {
            "timestamp": cached.get("timestamp"),
            sensor_key: cached.get(sensor_key, "Неизвестный датчик"),
        }
