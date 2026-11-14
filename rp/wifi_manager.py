import subprocess
import sys
import socket


class WiFiSystemManager:
    @staticmethod
    def get_local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    @staticmethod
    def switch_wifi_network(ssid: str, password: str = "") -> str:
        ssid = ssid.strip()
        if not ssid:
            return "[Wi-Fi OS] Ошибка: пустой SSID."

        try:
            if sys.platform.startswith("linux"):
                cmd = ["nmcli", "dev", "wifi", "connect", ssid]
                if password:
                    cmd += ["password", password]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if res.returncode == 0:
                    return f"[Wi-Fi OS] Успешно подключено к '{ssid}'. Новый IP: {WiFiSystemManager.get_local_ip()}"
                return f"[Wi-Fi OS] Ошибка nmcli: {res.stderr.strip() or res.stdout.strip()}"

            elif sys.platform.startswith("win"):
                res = subprocess.run(["netsh", "wlan", "connect", f"name={ssid}"], capture_output=True, text=True, timeout=10)
                if res.returncode == 0:
                    return f"[Wi-Fi OS] Команда переключения на '{ssid}' выполнена."
                return f"[Wi-Fi OS] Ошибка netsh: {res.stderr.strip() or res.stdout.strip()}"

            return "[Wi-Fi OS] Платформа не поддерживается для автосмены SSID."
        except Exception as e:
            return f"[Wi-Fi OS] Исключение при смене сети: {e}"
