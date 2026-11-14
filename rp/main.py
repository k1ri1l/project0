import time
import json
import math
import config
from hardware_stm32 import STM32Worker
from network_wifi import WiFiServer
from network_lora import RadioManager
from file_manager import FileManager
from wifi_manager import WiFiSystemManager


class RoverServerApp:
    def __init__(self):
        self.files = FileManager(downloads_dir="downloads")
        self.stm = STM32Worker(json_cache_path="telemetry_cache.json", on_obstacle_stop=self.on_emergency_stop)
        self.wifi = WiFiServer(on_command_callback=self.handle_incoming_command)
        self.lora = RadioManager(on_receive_callback=self.on_lora_text)

        self.x = 0.0
        self.y = 0.0
        self.target_x = None
        self.target_y = None

    def start(self):
        print("=== ЯДРО МАШИНКИ (meshrp v4.0) ЗАПУЩЕНО ===")
        self.stm.start()
        self.lora.scan_ports()
        self.lora.start_auto_rescan()
        self.wifi.start()

        try:
            while True:
                self._navigation_step()
                self.wifi.send_to_pc({
                    "type": "fast_state",
                    "pos": {"x": round(self.x, 1), "y": round(self.y, 1)},
                    "front_mm": self.stm.front_distance_mm,
                    "auto_stopped": self.stm.auto_stopped,
                })
                time.sleep(0.15)
        except KeyboardInterrupt:
            print("\nОстановка сервера машинки...")
            self.stm.send_motor_command(2, 0, 0)
            self.stm.stop()
            self.lora.close_all()

    def on_emergency_stop(self, dist_mm: int):
        self.target_x = None
        self.target_y = None
        self.wifi.send_to_pc({
            "type": "info",
            "msg": f"АВАРИЙНЫЙ СТОП! Препятствие на {dist_mm} мм (< 50 см).",
        })

    def _navigation_step(self):
        if self.target_x is None or self.target_y is None:
            return
        if self.stm.front_distance_mm < getattr(config, "STOP_DISTANCE_MM", 500):
            self.target_x = None
            self.target_y = None
            self.stm.send_motor_command(2, 0, 0)
            return

        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = math.hypot(dx, dy)
        if dist < 80.0:
            self.target_x = None
            self.target_y = None
            self.stm.send_motor_command(2, 0, 0)
            return

        spd = getattr(config, "DEFAULT_DRIVE_SPEED", 900)
        self.stm.send_motor_command(1, spd, spd)
        step = min(45.0, dist)
        self.x += (dx / dist) * step
        self.y += (dy / dist) * step

    def handle_incoming_command(self, pkt: dict, source: str = "wifi", sender: str = "^all"):
        cmd = pkt.get("cmd")

        if cmd == "DRIVE":
            self.target_x = self.target_y = None
            l_spd = int(pkt.get("L", 0))
            r_spd = int(pkt.get("R", 0))
            self.stm.send_motor_command(1, l_spd, r_spd)

        elif cmd == "STOP":
            self.target_x = self.target_y = None
            self.stm.send_motor_command(2, 0, 0)

        elif cmd == "TEST_DRV":
            self.target_x = self.target_y = None
            self.stm.send_motor_command(5, 0, 0)
            resp = {"type": "sensor_response", "sensor": "drivers", "data": self.stm.get_sensor_payload("drivers")}
            self._reply(resp, source, sender)

        elif cmd == "GOTO":
            self.target_x = float(pkt.get("x", 0.0))
            self.target_y = float(pkt.get("y", 0.0))

        elif cmd == "GET_SENSOR":
            s_key = pkt.get("sensor", "all")
            data = self.stm.get_sensor_payload(s_key)
            resp = {"type": "sensor_response", "sensor": s_key, "data": data}
            self._reply(resp, source, sender)

        elif cmd == "SET_WIFI":
            ssid = pkt.get("ssid", "")
            pwd = pkt.get("password", "")
            msg = WiFiSystemManager.switch_wifi_network(ssid, pwd)
            self._reply({"type": "info", "msg": msg}, source, sender)

        elif cmd == "CHUNK":
            res = self.files.process_chunk(pkt.get("data", ""))
            if res and res["type"] == "msg":
                print(f"[Получено сообщение]: {res['text']}")

    def _reply(self, resp_dict: dict, source: str, sender: str):
        if source == "wifi" and self.wifi.send_to_pc(resp_dict):
            return
        raw_str = json.dumps(resp_dict, ensure_ascii=False, separators=(",", ":"))
        self.lora.send_raw_text(raw_str, target=sender)

    def on_lora_text(self, text: str, sender: str, snr: float):
        if text.startswith(("CM|", "CD|")):
            res = self.files.process_chunk(text)
            if res and res["type"] == "msg":
                print(f"[LoRa Сообщение от {sender}]: {res['text']}")
            return

        if text.startswith("{") and text.endswith("}"):
            try:
                pkt = json.loads(text)
                self.handle_incoming_command(pkt, source="lora", sender=sender)
            except Exception:
                pass


if __name__ == "__main__":
    RoverServerApp().start()
