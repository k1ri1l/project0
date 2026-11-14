import os
import sys
import json
import time

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext
except ImportError:
    print("Ошибка: Для запуска GUI требуется Tkinter (sudo pacman -S tk).")
    sys.exit(1)

import config
from network_wifi import WiFiClient
from network_lora import RadioManager
from file_manager import FileManager
from ssh_manager import SSHManager
from wifi_manager import WiFiSystemManager


class RoverControlGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hybrid Rover Control Center v4.0 (Wi-Fi + Meshtastic + STM32)")
        self.root.geometry("1180x760")

        self.transport_mode = tk.StringVar(value=getattr(config, "TRANSPORT_MODE", "AUTO"))
        self.ip_var = tk.StringVar(value=getattr(config, "ROVER_IP", "192.168.1.100"))
        self.wifi_ssid_var = tk.StringVar(value="")
        self.wifi_pass_var = tk.StringVar(value="")
        self.lora_port_var = tk.StringVar(value="Авто")
        self.lora_target_var = tk.StringVar(value="^all (Всем в эфире)")
        self.msg_entry_var = tk.StringVar(value="")

        self.rover_x = 0.0
        self.rover_y = 0.0
        self.pressed_keys = set()

        self.files = FileManager(downloads_dir="downloads", on_log=self.log)
        self.lora = RadioManager(on_receive_callback=self.on_lora_rx, on_log=self.log)
        self.wifi = WiFiClient(
            on_packet_callback=self.on_wifi_rx,
            on_status_change=self.on_wifi_status,
            on_ip_discovered=self.on_ip_found,
            on_log=self.log,
        )

        self._build_ui()
        self._bind_arrow_keys()

        self.lora.start_auto_rescan()
        self.rescan_lora_nodes()
        self.wifi.discover_rover_ip()

    def _build_ui(self):
        left_panel = ttk.Frame(self.root, width=440, padding=8)
        left_panel.pack(side=tk.LEFT, fill=tk.Y)

        right_panel = ttk.Frame(self.root, padding=8)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 1. БЛОК СЕТИ, АВТОПОИСКА IP И ПЕРЕКЛЮЧЕНИЯ КАНАЛА
        net_box = ttk.LabelFrame(left_panel, text="1. Связь и Переключение (Wi-Fi / Meshtastic)", padding=6)
        net_box.pack(fill=tk.X, pady=4)

        mode_row = ttk.Frame(net_box)
        mode_row.pack(fill=tk.X, pady=2)
        ttk.Label(mode_row, text="Режим:").pack(side=tk.LEFT)
        for m_val, m_lbl in [("AUTO", "АВТО"), ("WIFI", "Только Wi-Fi"), ("LORA", "Только Mesh")]:
            ttk.Radiobutton(
                mode_row, text=m_lbl, value=m_val, variable=self.transport_mode, command=self._on_mode_change
            ).pack(side=tk.LEFT, padx=4)

        ip_row = ttk.Frame(net_box)
        ip_row.pack(fill=tk.X, pady=3)
        ttk.Label(ip_row, text="IP RPi:").pack(side=tk.LEFT)
        ttk.Entry(ip_row, textvariable=self.ip_var, width=15).pack(side=tk.LEFT, padx=4)
        ttk.Button(ip_row, text="Задать IP и Войти", command=self.apply_manual_ip).pack(side=tk.LEFT, padx=2)
        ttk.Button(ip_row, text="Найти IP", command=self.wifi.discover_rover_ip).pack(side=tk.LEFT, padx=2)

        conn_row = ttk.Frame(net_box)
        conn_row.pack(fill=tk.X, pady=2)
        self.lbl_wifi_state = ttk.Label(conn_row, text="Wi-Fi: ОТКЛЮЧЕНО", foreground="red", font=("Arial", 9, "bold"))
        self.lbl_wifi_state.pack(side=tk.LEFT)
        ttk.Button(conn_row, text="Отключить TCP", command=self.wifi.disconnect).pack(side=tk.RIGHT, padx=2)
        ttk.Button(conn_row, text="Запуск по SSH", command=self.run_ssh_boot).pack(side=tk.RIGHT, padx=2)

        # Смена Wi-Fi сети
        wlan_row = ttk.Frame(net_box)
        wlan_row.pack(fill=tk.X, pady=3)
        ttk.Label(wlan_row, text="SSID:").pack(side=tk.LEFT)
        ttk.Entry(wlan_row, textvariable=self.wifi_ssid_var, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Label(wlan_row, text="Pass:").pack(side=tk.LEFT)
        ttk.Entry(wlan_row, textvariable=self.wifi_pass_var, width=10, show="*").pack(side=tk.LEFT, padx=2)
        ttk.Button(wlan_row, text="Сменить Wi-Fi ПК", command=self.change_local_wifi).pack(side=tk.LEFT, padx=2)
        ttk.Button(wlan_row, text="Сменить на RPi", command=self.change_remote_wifi).pack(side=tk.LEFT, padx=2)

        # 2. БЛОК MESHTASTIC (АВТОПОРТ И ПОЛУЧАТЕЛИ)
        lora_box = ttk.LabelFrame(left_panel, text="2. Радиоузел Meshtastic (Автопорт и Ноды)", padding=6)
        lora_box.pack(fill=tk.X, pady=4)

        p_row = ttk.Frame(lora_box)
        p_row.pack(fill=tk.X, pady=2)
        ttk.Label(p_row, text="Порт:").pack(side=tk.LEFT)
        self.cb_lora_ports = ttk.Combobox(p_row, textvariable=self.lora_port_var, width=16, state="readonly")
        self.cb_lora_ports.pack(side=tk.LEFT, padx=4)
        ttk.Button(p_row, text="Найти порты и ноды", command=self.rescan_lora_nodes).pack(side=tk.LEFT, padx=2)

        t_row = ttk.Frame(lora_box)
        t_row.pack(fill=tk.X, pady=2)
        ttk.Label(t_row, text="Кому:").pack(side=tk.LEFT)
        self.cb_lora_targets = ttk.Combobox(t_row, textvariable=self.lora_target_var, width=32)
        self.cb_lora_targets.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)

        # 3. УПРАВЛЕНИЕ ДВИЖЕНИЕМ (КНОПКИ + СТРЕЛОЧКИ КЛАВИАТУРЫ)
        drv_box = ttk.LabelFrame(left_panel, text="3. Привод (Кнопки или Стрелочки ↑ ↓ ← →, Пробел=Стоп)", padding=6)
        drv_box.pack(fill=tk.X, pady=4)

        grid_f = ttk.Frame(drv_box)
        grid_f.pack()
        ttk.Button(grid_f, text="▲ ВПЕРЕД (↑)", width=14, command=lambda: self.send_drive("FORWARD")).grid(row=0, column=1, pady=2)
        ttk.Button(grid_f, text="◀ ВЛЕВО (←)", width=14, command=lambda: self.send_drive("LEFT")).grid(row=1, column=0, padx=2)
        ttk.Button(grid_f, text="■ СТОП (Space)", width=14, command=lambda: self.send_drive("STOP")).grid(row=1, column=1, padx=2)
        ttk.Button(grid_f, text="▶ ВПРАВО (→)", width=14, command=lambda: self.send_drive("RIGHT")).grid(row=1, column=2, padx=2)
        ttk.Button(grid_f, text="▼ НАЗАД (↓)", width=14, command=lambda: self.send_drive("BACKWARD")).grid(row=2, column=1, pady=2)

        # 4. ЗАПРОС ДАТЧИКОВ И ДРАЙВЕРОВ (ИЗ КЭША JSON RPi)
        sens_box = ttk.LabelFrame(left_panel, text="4. Запрос датчиков с RPi (JSON кэш 5 сек / Прямой опрос)", padding=6)
        sens_box.pack(fill=tk.X, pady=4)

        s_grid = ttk.Frame(sens_box)
        s_grid.pack(fill=tk.X)
        ttk.Button(s_grid, text="Все датчики (ALL)", command=lambda: self.request_sensor("all")).grid(row=0, column=0, padx=2, pady=2, sticky="ew")
        ttk.Button(s_grid, text="Ультразвук (US)", command=lambda: self.request_sensor("ultrasonic")).grid(row=0, column=1, padx=2, pady=2, sticky="ew")
        ttk.Button(s_grid, text="Гироскоп (IMU)", command=lambda: self.request_sensor("imu")).grid(row=0, column=2, padx=2, pady=2, sticky="ew")
        ttk.Button(s_grid, text="Драйверы (DRV)", command=lambda: self.request_sensor("drivers")).grid(row=1, column=0, padx=2, pady=2, sticky="ew")
        ttk.Button(s_grid, text="ИК-флаги (IR)", command=lambda: self.request_sensor("ir")).grid(row=1, column=1, padx=2, pady=2, sticky="ew")
        ttk.Button(s_grid, text="Тест движков", command=lambda: self.send_command_packet({"cmd": "TEST_DRV"})).grid(row=1, column=2, padx=2, pady=2, sticky="ew")

        self.lbl_fast_telem = ttk.Label(
            sens_box, text="Препятствие: -- мм | Защита <50см: НОРМА", font=("Arial", 9, "bold"), foreground="darkgreen"
        )
        self.lbl_fast_telem.pack(anchor=tk.W, pady=4)

        # 5. ПЕРЕДАЧА СООБЩЕНИЙ И ФАЙЛОВ ЧАНКАМИ
        io_box = ttk.LabelFrame(left_panel, text="5. Отправка Сообщений и Файлов (Чанками)", padding=6)
        io_box.pack(fill=tk.X, pady=4)

        m_row = ttk.Frame(io_box)
        m_row.pack(fill=tk.X, pady=2)
        ttk.Entry(m_row, textvariable=self.msg_entry_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        ttk.Button(m_row, text="Отправить соо", command=self.send_user_message).pack(side=tk.LEFT, padx=2)
        ttk.Button(m_row, text="Отправить файл...", command=self.send_user_file).pack(side=tk.LEFT, padx=2)

        # ПРАВАЯ ПАНЕЛЬ: КАРТА + ЖУРНАЛ ТЕЛЕМЕТРИИ
        map_box = ttk.LabelFrame(right_panel, text="Интерактивная карта (Клик левой кнопкой задает направление / точку поездки)", padding=4)
        map_box.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(map_box, bg="#f7f9fc", cursor="crosshair", height=380)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_map_click)
        self.rover_marker = self.canvas.create_oval(-7, -7, 7, 7, fill="#0055ff", outline="white", width=2)

        log_box = ttk.LabelFrame(right_panel, text="Консоль событий, чанков и JSON-ответов от датчиков", padding=4)
        log_box.pack(fill=tk.BOTH, expand=True, pady=(6, 0))

        self.txt_log = scrolledtext.ScrolledText(log_box, height=12, font=("Consolas", 9))
        self.txt_log.pack(fill=tk.BOTH, expand=True)

    def _bind_arrow_keys(self):
        # Управление на стрелочки (работает когда фокус не в текстовом поле ввода)
        self.root.bind("<KeyPress-Up>", lambda e: self._handle_key_drive("FORWARD"))
        self.root.bind("<KeyPress-Down>", lambda e: self._handle_key_drive("BACKWARD"))
        self.root.bind("<KeyPress-Left>", lambda e: self._handle_key_drive("LEFT"))
        self.root.bind("<KeyPress-Right>", lambda e: self._handle_key_drive("RIGHT"))
        self.root.bind("<space>", lambda e: self.send_drive("STOP"))

        self.root.bind("<KeyRelease-Up>", lambda e: self._handle_key_release("FORWARD"))
        self.root.bind("<KeyRelease-Down>", lambda e: self._handle_key_release("BACKWARD"))
        self.root.bind("<KeyRelease-Left>", lambda e: self._handle_key_release("LEFT"))
        self.root.bind("<KeyRelease-Right>", lambda e: self._handle_key_release("RIGHT"))

    def _handle_key_drive(self, direction: str):
        if isinstance(self.root.focus_get(), ttk.Entry):
            return
        if direction not in self.pressed_keys:
            self.pressed_keys.add(direction)
            self.send_drive(direction)

    def _handle_key_release(self, direction: str):
        if direction in self.pressed_keys:
            self.pressed_keys.discard(direction)
            if not self.pressed_keys:
                self.send_drive("STOP")

    def log(self, text: str):
        def _append():
            ts = time.strftime("%H:%M:%S")
            self.txt_log.insert(tk.END, f"[{ts}] {text}\n")
            self.txt_log.see(tk.END)
        self.root.after(0, _append)

    def _on_mode_change(self):
        mode = self.transport_mode.get()
        config.TRANSPORT_MODE = mode
        self.log(f"[Режим связи] Переключено на: {mode}")
        if mode == "LORA":
            self.wifi.disconnect()
        elif mode in ("WIFI", "AUTO") and not self.wifi.is_connected:
            self.wifi.connect_once()

    def apply_manual_ip(self):
        new_ip = self.ip_var.get().strip()
        self.wifi.set_target_ip(new_ip, reconnect=True)

    def on_ip_found(self, found_ip: str):
        self.root.after(0, lambda: self.ip_var.set(found_ip))
        if self.transport_mode.get() in ("AUTO", "WIFI") and not self.wifi.is_connected:
            self.wifi.connect_once()

    def on_wifi_status(self, is_online: bool, ip: str, reason: str):
        def _upd():
            if is_online:
                self.lbl_wifi_state.config(text=f"Wi-Fi: ОНЛАЙН ({ip})", foreground="green")
            else:
                self.lbl_wifi_state.config(text=f"Wi-Fi: ОФЛАЙН ({reason})", foreground="red")
        self.root.after(0, _upd)
        self.log(f"[Статус Wi-Fi] {ip} -> {'ОНЛАЙН' if is_online else 'ОФЛАЙН'} ({reason})")

    def run_ssh_boot(self):
        self.wifi.set_target_ip(self.ip_var.get().strip(), reconnect=False)
        SSHManager.start_rover_service(
            on_status=lambda s: self.log(f"[SSH] {s}"),
            on_done=lambda: self.wifi.connect_once(),
        )

    def change_local_wifi(self):
        ssid = self.wifi_ssid_var.get()
        pwd = self.wifi_pass_var.get()
        res = WiFiSystemManager.switch_wifi_network(ssid, pwd)
        self.log(res)

    def change_remote_wifi(self):
        ssid = self.wifi_ssid_var.get().strip()
        pwd = self.wifi_pass_var.get().strip()
        if not ssid:
            self.log("[Wi-Fi RPi] Введите SSID для смены сети на Raspberry Pi.")
            return
        self.send_command_packet({"cmd": "SET_WIFI", "ssid": ssid, "password": pwd})

    def rescan_lora_nodes(self):
        ports = self.lora.scan_ports()
        port_list = ["Авто"] + ports
        self.cb_lora_ports["values"] = port_list
        if ports:
            self.lora_port_var.set(ports[0])

        targets = self.lora.get_discovered_recipients()
        self.cb_lora_targets["values"] = targets
        if targets:
            self.lora_target_var.set(targets[0])

    def _use_wifi_now(self) -> bool:
        mode = self.transport_mode.get()
        if mode == "WIFI":
            return True
        if mode == "LORA":
            return False
        return self.wifi.is_connected

    def send_command_packet(self, pkt: dict):
        if self._use_wifi_now():
            if self.wifi.send_json(pkt):
                return
            if self.transport_mode.get() == "WIFI":
                self.log("[Ошибка] Wi-Fi отключен (режим 'Только Wi-Fi'). Нажмите 'Задать IP и Войти'.")
                return
            self.log("[Авто-Фоллбэк] Wi-Fi недоступен, отправка команды через Meshtastic...")

        # Отправка через LoRa компактным JSON
        raw_str = json.dumps(pkt, ensure_ascii=False, separators=(",", ":"))
        pref_port = None if self.lora_port_var.get() == "Авто" else self.lora_port_var.get()
        self.lora.send_raw_text(raw_str, target=self.lora_target_var.get(), preferred_port=pref_port)

    def send_drive(self, direction: str):
        spd = getattr(config, "DEFAULT_DRIVE_SPEED", 900)
        t_spd = getattr(config, "DEFAULT_TURN_SPEED", 650)
        mapping = {
            "FORWARD": (spd, spd),
            "BACKWARD": (-spd, -spd),
            "LEFT": (-t_spd, t_spd),
            "RIGHT": (t_spd, -t_spd),
            "STOP": (0, 0),
        }
        l_val, r_val = mapping.get(direction, (0, 0))
        if direction == "STOP":
            self.send_command_packet({"cmd": "STOP"})
        else:
            self.send_command_packet({"cmd": "DRIVE", "dir": direction, "L": l_val, "R": r_val})

    def request_sensor(self, sensor_name: str):
        self.send_command_packet({"cmd": "GET_SENSOR", "sensor": sensor_name})

    def send_user_message(self):
        txt = self.msg_entry_var.get().strip()
        if not txt:
            return
        self.msg_entry_var.set("")

        # Поддержка текстовых команд движения: "вперед", "назад", "влево", "вправо", "стоп"
        low = txt.lower()
        if low in ("вперед", "forward", "w"):
            self.send_drive("FORWARD")
            return
        if low in ("назад", "backward", "back", "s"):
            self.send_drive("BACKWARD")
            return
        if low in ("влево", "left", "a"):
            self.send_drive("LEFT")
            return
        if low in ("вправо", "right", "d"):
            self.send_drive("RIGHT")
            return
        if low in ("стоп", "stop", "q"):
            self.send_drive("STOP")
            return

        via_wifi = self._use_wifi_now()
        chunks = self.files.slice_text(txt, via_wifi=via_wifi)
        self._dispatch_chunks(chunks, via_wifi, label=f"Сообщение ({len(chunks)} ч.)")

    def send_user_file(self):
        filepath = filedialog.askopenfilename(title="Выберите файл для отправки чанками")
        if not filepath:
            return
        via_wifi = self._use_wifi_now()
        chunks = self.files.slice_file(filepath, via_wifi=via_wifi)
        self._dispatch_chunks(chunks, via_wifi, label=f"Файл '{os.path.basename(filepath)}' ({len(chunks)} ч.)")

    def _dispatch_chunks(self, chunks: list[str], via_wifi: bool, label: str):
        if via_wifi:
            ok_all = True
            for ch in chunks:
                if not self.wifi.send_json({"cmd": "CHUNK", "data": ch}):
                    ok_all = False
                    break
            if ok_all:
                self.log(f"[Wi-Fi Чанки] Отправлено: {label}")
                return
            if self.transport_mode.get() == "WIFI":
                self.log("[Ошибка] Обрыв Wi-Fi при передаче чанков.")
                return

        pref_port = None if self.lora_port_var.get() == "Авто" else self.lora_port_var.get()
        ok = self.lora.send_chunks(chunks, target=self.lora_target_var.get(), preferred_port=pref_port)
        if ok:
            self.log(f"[LoRa Чанки] Отправлено по радио: {label}")

    def on_wifi_rx(self, pkt: dict):
        ptype = pkt.get("type")
        if ptype == "fast_state":
            pos = pkt.get("pos", {})
            self.rover_x = pos.get("x", 0.0)
            self.rover_y = pos.get("y", 0.0)
            dist = pkt.get("front_mm", 2000)
            obstacle = pkt.get("auto_stopped", False)

            def _ui():
                st_txt = "АВАРИЙНЫЙ СТОП (<50см)!" if obstacle else "НОРМА"
                color = "red" if obstacle else "darkgreen"
                self.lbl_fast_telem.config(text=f"Препятствие: {dist} мм | Защита <50см: {st_txt}", foreground=color)
                self._redraw_map()
            self.root.after(0, _ui)

        elif ptype == "sensor_response":
            sensor_key = pkt.get("sensor", "all")
            data = pkt.get("data", {})
            pretty = json.dumps(data, ensure_ascii=False, indent=2)
            self.log(f"[Ответ датчика '{sensor_key}']:\n{pretty}")

        elif ptype == "chunk":
            res = self.files.process_chunk(pkt.get("data", ""))
            if res and res["type"] == "msg":
                self.log(f"[Входящее сообщение Wi-Fi]: {res['text']}")

        elif ptype == "info":
            self.log(f"[RPi Инфо]: {pkt.get('msg')}")

    def on_lora_rx(self, text: str, sender: str, snr: float):
        # Обновляем выпадающий список доступных получателей при приеме пакета
        self.root.after(0, lambda: self.cb_lora_targets.configure(values=self.lora.get_discovered_recipients()))

        if text.startswith(("CM|", "CD|")):
            res = self.files.process_chunk(text)
            if res and res["type"] == "msg":
                self.log(f"[LoRa Сообщение от {sender} (SNR {snr})]: {res['text']}")
            return

        if text.startswith("{") and text.endswith("}"):
            try:
                pkt = json.loads(text)
                if pkt.get("type") == "sensor_response":
                    pretty = json.dumps(pkt.get("data", {}), ensure_ascii=False, indent=2)
                    self.log(f"[LoRa Датчик '{pkt.get('sensor')}' от {sender}]:\n{pretty}")
                    return
            except Exception:
                pass

        self.log(f"[LoRa от {sender} (SNR {snr} dB)]: {text}")

    def _redraw_map(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        if w <= 2 or h <= 2:
            return
        cx, cy = w / 2, h / 2
        sx = cx + self.rover_x * 0.1
        sy = cy - self.rover_y * 0.1
        self.canvas.coords(self.rover_marker, sx - 7, sy - 7, sx + 7, sy + 7)

    def _on_map_click(self, event):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        tx = round((event.x - w / 2) / 0.1, 1)
        ty = round((h / 2 - event.y) / 0.1, 1)
        self.canvas.delete("target")
        self.canvas.create_oval(event.x - 6, event.y - 6, event.x + 6, event.y + 6, outline="red", width=2, tags="target")
        self.send_command_packet({"cmd": "GOTO", "x": tx, "y": ty})


if __name__ == "__main__":
    root = tk.Tk()
    app = RoverControlGUI(root)
    root.mainloop()
