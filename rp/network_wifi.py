import socket
import threading
import json
import time
import config
from wifi_manager import WiFiSystemManager


class WiFiServer:
    def __init__(self, on_command_callback=None):
        self.port = getattr(config, "ROVER_PORT", 5000)
        self.disc_port = getattr(config, "DISCOVERY_PORT", 5005)
        self.on_command_callback = on_command_callback
        self.client_conn = None
        self.running = False

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._tcp_accept_loop, daemon=True).start()
        threading.Thread(target=self._udp_discovery_responder, daemon=True).start()

    def _tcp_accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(2)
        print(f"[Wi-Fi Сервер RPi] Слушает TCP порт {self.port} (Локальный IP: {WiFiSystemManager.get_local_ip()})")

        while self.running:
            try:
                conn, addr = srv.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                print(f"[Wi-Fi] Подключен клиент ПК: {addr[0]}")
                self.client_conn = conn
                threading.Thread(target=self._client_rx_loop, args=(conn, addr[0]), daemon=True).start()
            except Exception:
                time.sleep(0.5)

    def _client_rx_loop(self, conn: socket.socket, ip: str):
        with conn:
            try:
                f = conn.makefile("r", encoding="utf-8")
                for line in f:
                    if not line:
                        break
                    pkt = json.loads(line.strip())
                    if self.on_command_callback:
                        self.on_command_callback(pkt, source="wifi", sender=ip)
            except Exception:
                pass
        if self.client_conn == conn:
            self.client_conn = None
        print(f"[Wi-Fi] Клиент {ip} отключился.")

    def send_to_pc(self, pkt: dict) -> bool:
        conn = self.client_conn
        if not conn:
            return False
        try:
            raw = json.dumps(pkt, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            conn.sendall(raw)
            return True
        except Exception:
            self.client_conn = None
            return False

    def _udp_discovery_responder(self):
        # Слушает широковещательные запросы от ПК и периодически рассылает свой IP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.bind(("0.0.0.0", self.disc_port))
            sock.settimeout(2.0)
        except Exception:
            return

        while self.running:
            my_ip = WiFiSystemManager.get_local_ip()
            beacon = json.dumps({"magic": "ROVER_HERE", "rover_ip": my_ip, "port": self.port}).encode("utf-8")
            try:
                sock.sendto(beacon, ("255.255.255.255", self.disc_port))
            except Exception:
                pass

            try:
                data, addr = sock.recvfrom(1024)
                req = json.loads(data.decode("utf-8"))
                if req.get("magic") == "ROVER_DISCOVER":
                    sock.sendto(beacon, (addr[0], self.disc_port))
            except socket.timeout:
                pass
            except Exception:
                pass
