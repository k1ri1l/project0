import socket
import threading
import json
import time
import config
from wifi_manager import WiFiSystemManager


class WiFiClient:
    def __init__(self, on_packet_callback=None, on_status_change=None, on_ip_discovered=None, on_log=None):
        self.target_ip = getattr(config, "ROVER_IP", "192.168.1.100")
        self.port = getattr(config, "ROVER_PORT", 5000)
        self.disc_port = getattr(config, "DISCOVERY_PORT", 5005)

        self.on_packet_callback = on_packet_callback
        self.on_status_change = on_status_change
        self.on_ip_discovered = on_ip_discovered
        self.on_log = on_log or (lambda m: print(m))

        self.sock = None
        self.is_connected = False
        self._last_reported_state = None
        self._connect_lock = threading.Lock()
        self._running = True

        threading.Thread(target=self._udp_discovery_listener, daemon=True).start()

    def _set_state(self, new_state: bool, reason: str = ""):
        self.is_connected = new_state
        if self._last_reported_state == new_state:
            return
        self._last_reported_state = new_state
        if self.on_status_change:
            self.on_status_change(new_state, self.target_ip, reason)

    def set_target_ip(self, new_ip: str, reconnect: bool = True):
        new_ip = new_ip.strip()
        if not new_ip:
            return
        self.target_ip = new_ip
        config.ROVER_IP = new_ip
        self.on_log(f"[TCP] Целевой IP изменен на {self.target_ip}")
        if reconnect:
            self.disconnect()
            self.connect_once()

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self._set_state(False, "Отключено вручную")

    def connect_once(self):
        # Строго одна попытка подключения без бесконечного цикла переподключений!
        def _attempt():
            with self._connect_lock:
                if self.is_connected:
                    return
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    s.settimeout(2.5)
                    s.connect((self.target_ip, self.port))
                    s.settimeout(None)
                    self.sock = s
                    self._set_state(True, "TCP Онлайн")
                    self._rx_loop(s)
                except Exception as e:
                    self.sock = None
                    self._set_state(False, f"Недоступен ({e})")

        threading.Thread(target=_attempt, daemon=True).start()

    def _rx_loop(self, sock: socket.socket):
        try:
            f = sock.makefile("r", encoding="utf-8")
            for line in f:
                if not line:
                    break
                pkt = json.loads(line.strip())
                if self.on_packet_callback:
                    self.on_packet_callback(pkt)
        except Exception:
            pass
        finally:
            self.sock = None
            self._set_state(False, "Соединение разорвано")

    def send_json(self, data: dict) -> bool:
        if not self.is_connected or not self.sock:
            return False
        try:
            raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            self.sock.sendall(raw)
            return True
        except Exception:
            self.sock = None
            self._set_state(False, "Обрыв при отправке")
            return False

    def discover_rover_ip(self):
        def _scan():
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.settimeout(1.5)
                    probe = json.dumps({" magic": "ROVER_DISCOVER", "pc_ip": WiFiSystemManager.get_local_ip()}).encode("utf-8")
                    s.sendto(probe, ("255.255.255.255", self.disc_port))
            except Exception as e:
                self.on_log(f"[UDP Поиск] Ошибка отправки маяка: {e}")

        threading.Thread(target=_scan, daemon=True).start()

    def _udp_discovery_listener(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", self.disc_port))
        except Exception:
            return

        while self._running:
            try:
                data, addr = sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                if msg.get("magic") == "ROVER_HERE":
                    found_ip = msg.get("rover_ip") or addr[0]
                    if found_ip and found_ip != self.target_ip:
                        self.target_ip = found_ip
                        config.ROVER_IP = found_ip
                        self.on_log(f"[UDP Автопоиск] Найден Ровер в сети: {found_ip}")
                        if self.on_ip_discovered:
                            self.on_ip_discovered(found_ip)
            except Exception:
                pass
