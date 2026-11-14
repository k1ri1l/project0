import time
import glob
import sys
import threading
import config

try:
    import meshtastic.serial_interface
    from pubsub import pub
    from serial.tools import list_ports
    MESHTASTIC_AVAILABLE = True
except ImportError:
    MESHTASTIC_AVAILABLE = False


class RadioManager:
    # Известные USB Vendor ID радиомодемов (Espressif ESP32-S3, Silicon Labs CP210x, QinHeng CH340/CH9102, RakWireless/Nordic)
    LORA_VIDS = {0x303A, 0x10C4, 0x1A86, 0x239A, 0x1915}

    def __init__(self, on_receive_callback=None, on_log=None):
        self.radios = {}          # port_path -> SerialInterface
        self.known_peers = {}     # node_id -> {"name": str, "snr": float, "last_seen": float, "via_port": str}
        self.on_receive_callback = on_receive_callback
        self.on_log = on_log or (lambda msg: print(msg))
        self._subscribed = False
        self._lock = threading.Lock()
        self._running = False

    def start_auto_rescan(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._hotplug_loop, daemon=True).start()

    def _hotplug_loop(self):
        # Автоматический подхват модема Meshtastic, если его воткнули по USB уже после запуска скрипта!
        while self._running:
            if not self.radios:
                self.scan_ports(silent_if_empty=True)
            time.sleep(3.0)

    def scan_ports(self, silent_if_empty: bool = False) -> list[str]:
        if not MESHTASTIC_AVAILABLE:
            if not silent_if_empty:
                self.on_log("[LoRa] Библиотека meshtastic не установлена.")
            return []

        with self._lock:
            candidate_ports = []
            try:
                for p in list_ports.comports():
                    desc = (p.description or "").upper()
                    mfg = (p.manufacturer or "").upper()
                    # Пропускаем ТОЛЬКО реальный чип STM32 (VID=0x0483), а НЕ имя порта /dev/ttyACM0!
                    if p.vid == 0x0483 or "STM32" in desc or "STMICROELECTRONICS" in mfg:
                        continue
                    if p.vid in self.LORA_VIDS or "USB" in p.device or "ACM" in p.device or "COM" in p.device:
                        candidate_ports.append(p.device)
            except Exception:
                if sys.platform.startswith("win"):
                    candidate_ports = [f"COM{i}" for i in range(1, 25)]
                else:
                    candidate_ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))

            for dev in candidate_ports:
                if dev in self.radios:
                    continue
                try:
                    iface = meshtastic.serial_interface.SerialInterface(devPath=dev)
                    if iface and getattr(iface, "myInfo", None):
                        self.radios[dev] = iface
                        my_id = self.get_my_id(iface)
                        self.on_log(f"[LoRa] Подключен модем Meshtastic на {dev} (Мой ID: {my_id})")
                        self._harvest_nodes_from_iface(dev, iface)
                    else:
                        iface.close()
                except Exception:
                    pass

            if self.radios and not self._subscribed:
                pub.subscribe(self._on_packet, "meshtastic.receive")
                self._subscribed = True

            return list(self.radios.keys())

    def _harvest_nodes_from_iface(self, dev: str, iface):
        try:
            my_id = self.get_my_id(iface)
            for node_id, info in (iface.nodes or {}).items():
                if node_id == my_id:
                    continue
                user = info.get("user", {})
                short_name = user.get("shortName") or user.get("longName") or node_id
                snr = info.get("snr", 0.0)
                self.known_peers[node_id] = {
                    "name": f"{short_name} ({node_id})",
                    "snr": snr,
                    "last_seen": time.time(),
                    "via_port": dev,
                }
        except Exception:
            pass

    @staticmethod
    def get_my_id(iface) -> str:
        try:
            return f"!{iface.myInfo.my_node_num:08x}"
        except Exception:
            return "unknown"

    def get_discovered_recipients(self) -> list[str]:
        for dev, iface in list(self.radios.items()):
            self._harvest_nodes_from_iface(dev, iface)
        result = ["^all (Всем в эфире)"]
        for nid, meta in self.known_peers.items():
            result.append(f"{nid} | {meta['name']}")
        return result

    def _on_packet(self, packet, interface):
        try:
            sender = packet.get("fromId", "unknown")
            snr = packet.get("rxSnr", 0.0)
            if sender and sender != "unknown":
                existing = self.known_peers.get(sender, {})
                self.known_peers[sender] = {
                    "name": existing.get("name", sender),
                    "snr": snr,
                    "last_seen": time.time(),
                    "via_port": existing.get("via_port", "LoRa"),
                }
            decoded = packet.get("decoded", {})
            text = decoded.get("text", "")
            if text and self.on_receive_callback:
                self.on_receive_callback(text, sender, snr)
        except Exception as e:
            self.on_log(f"[LoRa RX Ошибка]: {e}")

    def send_raw_text(self, text: str, target: str = "^all", preferred_port: str = None) -> bool:
        if not self.radios:
            self.scan_ports(silent_if_empty=True)

        iface = None
        if preferred_port and preferred_port in self.radios:
            iface = self.radios[preferred_port]
        else:
            iface = next(iter(self.radios.values()), None)

        if not iface:
            self.on_log("[LoRa] Нет подключенного радио-модема!")
            return False

        clean_target = target.split("|")[0].strip() if "|" in target else target.strip()
        try:
            if clean_target in ("^all", "all", "", "wifi") or clean_target.startswith("^all"):
                iface.sendText(text)
            else:
                iface.sendText(text, destinationId=clean_target)
            return True
        except Exception as e:
            self.on_log(f"[LoRa TX Ошибка]: {e}")
            return False

    def send_chunks(self, chunks: list[str], target: str = "^all", preferred_port: str = None) -> bool:
        delay = getattr(config, "CHUNK_DELAY", 0.6)
        for i, ch in enumerate(chunks, 1):
            ok = self.send_raw_text(ch, target=target, preferred_port=preferred_port)
            if not ok:
                return False
            if len(chunks) > 1 and i < len(chunks):
                time.sleep(delay)
        return True

    def close_all(self):
        self._running = False
        for iface in self.radios.values():
            try:
                iface.close()
            except Exception:
                pass
        self.radios.clear()
