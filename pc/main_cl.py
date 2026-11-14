import sys
import time
import json
import config
from network_wifi import WiFiClient
from network_lora import RadioManager
from file_manager import FileManager
from wifi_manager import WiFiSystemManager
from ssh_manager import SSHManager


class RoverControlCLI:
    def __init__(self):
        self.mode = getattr(config, "TRANSPORT_MODE", "AUTO")
        self.files = FileManager(downloads_dir="downloads")
        self.lora = RadioManager(on_receive_callback=self.on_lora_rx)
        self.wifi = WiFiClient(
            on_packet_callback=self.on_wifi_rx,
            on_status_change=self.on_wifi_status,
            on_ip_discovered=self.on_ip_found,
        )

    def start(self):
        print("=== КОНСОЛЬНЫЙ ЦЕНТР УПРАВЛЕНИЯ МАШИНКОЙ v4.0 ===")
        self.lora.scan_ports()
        self.lora.start_auto_rescan()
        self.wifi.discover_rover_ip()
        if self.mode in ("AUTO", "WIFI"):
            self.wifi.connect_once()
        self.print_help()
        self.repl()

    @staticmethod
    def print_help():
        print("\nДоступные команды:")
        print("  w / s / a / d / стоп          — Движение вперед, назад, влево, вправо, стоп")
        print("  /goto <X> <Y>                 — Задать точку направления движения (в мм)")
        print("  /sensor <all|imu|us|ir|drv>   — Запросить данные датчиков/драйверов из JSON-кэша RPi")
        print("  /test_drv                     — Физический самотест 4 моторов через MPU-6050")
        print("  /msg <текст>                  — Отправить сообщение чанками")
        print("  /file <путь>                  — Отправить файл чанками")
        print("  /mode <auto|wifi|lora>        — Вручную переключить канал связи")
        print("  /ip <IP_адрес>                — Задать IP Raspberry Pi/ПК и подключиться (1 попытка)")
        print("  /find_ip                      — Автопоиск IP машинки в локальной сети (UDP)")
        print("  /wifi <SSID> [пароль]         — Сменить Wi-Fi сеть на этом устройстве")
        print("  /rpi_wifi <SSID> [пароль]     — Сменить Wi-Fi сеть на Raspberry Pi")
        print("  /nodes                        — Список найденных радио-портов и нод-получателей")
        print("  /ssh_start                    — Запустить/перезапустить сервер на RPi по SSH")
        print("  /exit                         — Выход\n")

    def _use_wifi(self) -> bool:
        if self.mode == "WIFI":
            return True
        if self.mode == "LORA":
            return False
        return self.wifi.is_connected

    def send_pkt(self, pkt: dict):
        if self._use_wifi():
            if self.wifi.send_json(pkt):
                return
            if self.mode == "WIFI":
                print("[Ошибка] Нет связи по Wi-Fi (включен режим только Wi-Fi).")
                return
        raw = json.dumps(pkt, ensure_ascii=False, separators=(",", ":"))
        self.lora.send_raw_text(raw)

    def on_wifi_status(self, state: bool, ip: str, reason: str):
        print(f"\n[Wi-Fi Статус] {ip}: {'ОНЛАЙН' if state else 'ОФЛАЙН'} ({reason})")

    def on_ip_found(self, ip: str):
        print(f"\n[Автопоиск IP] Обнаружена Raspberry Pi: {ip}")
        if self.mode in ("AUTO", "WIFI") and not self.wifi.is_connected:
            self.wifi.connect_once()

    def on_wifi_rx(self, pkt: dict):
        t = pkt.get("type")
        if t == "sensor_response":
            print(f"\n[Датчик {pkt.get('sensor')}]:\n{json.dumps(pkt.get('data'), ensure_ascii=False, indent=2)}")
        elif t == "chunk":
            res = self.files.process_chunk(pkt.get("data", ""))
            if res and res["type"] == "msg":
                print(f"\n[Сообщение Wi-Fi]: {res['text']}")
        elif t == "fast_state" and pkt.get("auto_stopped"):
            print(f"\n[ВНИМАНИЕ] Мгновенная остановка STM32! До препятствия {pkt.get('front_mm')} мм (< 50 см)!")

    def on_lora_rx(self, text: str, sender: str, snr: float):
        if text.startswith(("CM|", "CD|")):
            res = self.files.process_chunk(text)
            if res and res["type"] == "msg":
                print(f"\n[LoRa Сообщение от {sender}]: {res['text']}")
            return
        print(f"\n[LoRa от {sender} (SNR {snr})]: {text}")

    def repl(self):
        spd = getattr(config, "DEFAULT_DRIVE_SPEED", 900)
        t_spd = getattr(config, "DEFAULT_TURN_SPEED", 650)

        while True:
            try:
                line = input(">> ").strip()
                if not line:
                    continue
                low = line.lower()

                if low in ("w", "вперед", "forward"):
                    self.send_pkt({"cmd": "DRIVE", "dir": "FORWARD", "L": spd, "R": spd})
                elif low in ("s", "назад", "backward"):
                    self.send_pkt({"cmd": "DRIVE", "dir": "BACKWARD", "L": -spd, "R": -spd})
                elif low in ("a", "влево", "left"):
                    self.send_pkt({"cmd": "DRIVE", "dir": "LEFT", "L": -t_spd, "R": t_spd})
                elif low in ("d", "вправо", "right"):
                    self.send_pkt({"cmd": "DRIVE", "dir": "RIGHT", "L": t_spd, "R": -t_spd})
                elif low in ("q", "стоп", "stop"):
                    self.send_pkt({"cmd": "STOP"})
                elif low == "/exit":
                    self.wifi.disconnect()
                    self.lora.close_all()
                    break
                elif low == "/help":
                    self.print_help()
                elif low.startswith("/mode "):
                    m = line.split(maxsplit=1)[1].upper()
                    if m in ("AUTO", "WIFI", "LORA"):
                        self.mode = m
                        print(f"[Режим] Установлен: {self.mode}")
                        if m == "LORA":
                            self.wifi.disconnect()
                        elif not self.wifi.is_connected:
                            self.wifi.connect_once()
                elif low.startswith("/ip "):
                    new_ip = line.split(maxsplit=1)[1]
                    self.wifi.set_target_ip(new_ip, reconnect=True)
                elif low == "/find_ip":
                    self.wifi.discover_rover_ip()
                elif low.startswith("/wifi "):
                    parts = line.split()
                    ssid = parts[1]
                    pwd = parts[2] if len(parts) > 2 else ""
                    print(WiFiSystemManager.switch_wifi_network(ssid, pwd))
                elif low.startswith("/rpi_wifi "):
                    parts = line.split()
                    ssid = parts[1]
                    pwd = parts[2] if len(parts) > 2 else ""
                    self.send_pkt({"cmd": "SET_WIFI", "ssid": ssid, "password": pwd})
                elif low == "/nodes":
                    ports = self.lora.scan_ports()
        self.lora.start_auto_rescan()
                    print(f"[LoRa Порты]: {ports}")
                    print(f"[Доступные получатели]: {self.lora.get_discovered_recipients()}")
                elif low.startswith("/sensor"):
                    parts = line.split()
                    s_key = parts[1].lower() if len(parts) > 1 else "all"
                    mapping = {"us": "ultrasonic", "drv": "drivers", "imu": "imu", "ir": "ir", "all": "all"}
                    self.send_pkt({"cmd": "GET_SENSOR", "sensor": mapping.get(s_key, s_key)})
                elif low == "/test_drv":
                    self.send_pkt({"cmd": "TEST_DRV"})
                elif low.startswith("/goto "):
                    parts = line.split()
                    if len(parts) == 3:
                        self.send_pkt({"cmd": "GOTO", "x": float(parts[1]), "y": float(parts[2])})
                elif low.startswith("/msg "):
                    msg_txt = line.split(maxsplit=1)[1]
                    via_w = self._use_wifi()
                    chunks = self.files.slice_text(msg_txt, via_wifi=via_w)
                    if via_w:
                        for c in chunks:
                            self.wifi.send_json({"cmd": "CHUNK", "data": c})
                    else:
                        self.lora.send_chunks(chunks)
                elif low.startswith("/file "):
                    fpath = line.split(maxsplit=1)[1]
                    if os.path.exists(fpath):
                        via_w = self._use_wifi()
                        chunks = self.files.slice_file(fpath, via_wifi=via_w)
                        if via_w:
                            for c in chunks:
                                self.wifi.send_json({"cmd": "CHUNK", "data": c})
                        else:
                            self.lora.send_chunks(chunks)
                    else:
                        print("[Ошибка] Файл не найден.")
                elif low == "/ssh_start":
                    SSHManager.start_rover_service(print, lambda: self.wifi.connect_once())
                else:
                    print("Неизвестная команда. Введите /help")
            except (KeyboardInterrupt, EOFError):
                break


if __name__ == "__main__":
    RoverControlCLI().start()
