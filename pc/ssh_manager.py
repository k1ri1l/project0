import threading
import time
import config

try:
    import paramiko
except ImportError:
    paramiko = None


class SSHManager:
    @staticmethod
    def start_rover_service(on_status, on_done=None):
        if not paramiko:
            on_status("Ошибка: не установлен paramiko (pip install paramiko)")
            return

        def _worker():
            ip = getattr(config, "ROVER_IP", "192.168.1.100")
            on_status(f"SSH подключение к {ip}...")
            try:
                cli = paramiko.SSHClient()
                cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                cli.connect(
                    ip,
                    username=getattr(config, "SSH_USER", "dytt"),
                    password=getattr(config, "SSH_PASS", "raspberry"),
                    timeout=5.0,
                )
                proj = getattr(config, "RPI_PROJECT_DIR", "meshrp")
                venv = getattr(config, "VENV_NAME", "venv")
                cmd = (
                    f"pkill -9 -f main.py; "
                    f"cd ~/{proj} || cd {proj}; "
                    f"(. {venv}/bin/activate || . env/bin/activate || true); "
                    f"nohup python3 main.py > rover.log 2>&1 &"
                )
                cli.exec_command(cmd)
                time.sleep(1.5)
                cli.close()
                on_status("Ядро на RPi перезапущено!")
                if on_done:
                    on_done()
            except Exception as e:
                on_status(f"Ошибка SSH: {e}")

        threading.Thread(target=_worker, daemon=True).start()
