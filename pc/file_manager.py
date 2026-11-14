import os
import time
import zlib
import base64
import zstandard as zstd
import config


class FileManager:
    def __init__(self, downloads_dir="downloads", on_log=None):
        self.downloads_dir = downloads_dir
        os.makedirs(self.downloads_dir, exist_ok=True)
        self.compressor = zstd.ZstdCompressor(level=6)
        self.decompressor = zstd.ZstdDecompressor()
        self.rx_buffers = {}
        self.on_log = on_log or (lambda msg: print(msg))

    def slice_data(self, raw_bytes: bytes, name: str, is_file: bool = True, via_wifi: bool = True) -> list[str]:
        comp = self.compressor.compress(raw_bytes)
        b85 = base64.b85encode(comp).decode("ascii")
        sid = f"{zlib.crc32(raw_bytes) & 0xFFFF:04x}"
        kind = "F" if is_file else "M"
        safe_name = os.path.basename(name)[:32] if is_file else "TEXT_MSG"

        max_payload = (
            getattr(config, "WIFI_CHUNK_SIZE", 32768)
            if via_wifi
            else max(getattr(config, "CHUNK_SIZE", 180) - 26, 80)
        )

        slices = [b85[i : i + max_payload] for i in range(0, len(b85), max_payload)] or [""]
        total = len(slices)

        packets = [f"CM|{sid}|{kind}|{total}|{safe_name}|{zlib.crc32(raw_bytes) & 0xFFFFFFFF}"]
        for idx, part in enumerate(slices, 1):
            packets.append(f"CD|{sid}|{idx}/{total}|{part}")
        return packets

    def slice_file(self, filepath: str, via_wifi: bool = True) -> list[str]:
        with open(filepath, "rb") as f:
            raw_bytes = f.read()
        return self.slice_data(raw_bytes, os.path.basename(filepath), is_file=True, via_wifi=via_wifi)

    def slice_text(self, text: str, via_wifi: bool = False) -> list[str]:
        raw_bytes = text.encode("utf-8")
        return self.slice_data(raw_bytes, "msg.txt", is_file=False, via_wifi=via_wifi)

    def process_chunk(self, text: str) -> dict | None:
        if text.startswith("CM|"):
            parts = text.split("|", 5)
            if len(parts) == 6:
                _, sid, kind, total_str, name, crc_str = parts
                self.rx_buffers[sid] = {
                    "kind": kind,
                    "name": os.path.basename(name),
                    "total": int(total_str),
                    "crc32": int(crc_str),
                    "parts": {},
                    "t0": time.perf_counter(),
                }
                label = "файла" if kind == "F" else "сообщения"
                self.on_log(f"[Чанки] Старт приема {label} '{name}' ({total_str} ч.)")
            return None

        if text.startswith("CD|"):
            parts = text.split("|", 3)
            if len(parts) != 4:
                return None
            _, sid, idx_total, payload = parts
            if "/" not in idx_total:
                return None
            idx_s, total_s = idx_total.split("/", 1)
            idx, total = int(idx_s), int(total_s)

            if sid not in self.rx_buffers:
                self.rx_buffers[sid] = {
                    "kind": "F",
                    "name": f"chunked_{sid}.bin",
                    "total": total,
                    "crc32": None,
                    "parts": {},
                    "t0": time.perf_counter(),
                }

            buf = self.rx_buffers[sid]
            buf["parts"][idx] = payload
            got = len(buf["parts"])

            if got >= buf["total"]:
                try:
                    full_b85 = "".join(buf["parts"][i] for i in range(1, buf["total"] + 1))
                    comp_bytes = base64.b85decode(full_b85.encode("ascii"))
                    raw_bytes = self.decompressor.decompress(comp_bytes)
                except Exception as e:
                    self.on_log(f"[Чанки Ошибка сборки {sid}]: {e}")
                    del self.rx_buffers[sid]
                    return None

                crc_ok = True
                if buf["crc32"] is not None:
                    crc_ok = (zlib.crc32(raw_bytes) & 0xFFFFFFFF) == buf["crc32"]

                elapsed = round(time.perf_counter() - buf["t0"], 2)
                kind = buf["kind"]
                name = buf["name"]
                del self.rx_buffers[sid]

                if kind == "F":
                    save_path = os.path.join(self.downloads_dir, name)
                    with open(save_path, "wb") as f:
                        f.write(raw_bytes)
                    self.on_log(
                        f"[Файл собран] {save_path} ({len(raw_bytes)} Б за {elapsed} с, CRC={'OK' if crc_ok else 'ERR'})"
                    )
                    return {"type": "file", "path": save_path, "size": len(raw_bytes), "crc_ok": crc_ok}
                else:
                    msg_text = raw_bytes.decode("utf-8", errors="replace")
                    return {"type": "msg", "text": msg_text, "crc_ok": crc_ok}

        return None
