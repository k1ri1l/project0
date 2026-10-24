import argparse
import base64
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2

from object_detector import (
    DetectorConfig,
    create_debug_view,
    create_ocr_debug_view,
    load_detector_config,
    process_image,
    save_detector_config,
)


CATEGORY_FILE = Path(__file__).resolve().parent / "categories.json"
DEFAULT_CATEGORIES = {
    1: "Острые и скругленные",
    2: "Другое",
}
AUTO_CATEGORY_LABEL = "Автоматически"


PARAMETER_GROUPS = (
    (
        "Изображение и контуры",
        (
            ("target_width", "Ширина кадра", int, 160, 1920, 10),
            ("target_height", "Высота кадра", int, 120, 1080, 10),
            ("blur_kernel", "Gaussian kernel (нечётный)", int, 1, 31, 2),
            ("canny_low", "Canny: нижний порог", int, 0, 255, 1),
            ("canny_high", "Canny: верхний порог", int, 1, 255, 1),
            ("morph_iterations", "Dilate/Erode: итерации", int, 0, 10, 1),
            ("min_area", "Минимальная площадь", float, 0, 50000, 50),
        ),
    ),
    (
        "Форма и ID",
        (
            ("similarity_threshold", "Сходство формы", float, 0, 0.5, 0.005),
            ("corner_epsilon", "Чувствительность углов", float, 0.001, 0.1, 0.001),
            ("rectangularity_threshold", "Прямоугольность", float, 0, 1, 0.01),
            ("sharp_max_vertices", "Вершин для sharp", int, 3, 12, 1),
            ("rounded_max_vertices", "Вершин для rounded", int, 4, 30, 1),
        ),
    ),
    (
        "Надпись внутри плашки",
        (
            ("signed_threshold", "Порог signed", float, 0, 0.5, 0.001),
            ("detail_threshold", "Контраст штриха", int, 0, 255, 1),
            ("min_detail_area", "Мин. площадь штриха", int, 1, 500, 1),
            ("interior_margin_x", "Поле по X", float, 0, 0.45, 0.01),
            ("interior_margin_y", "Поле по Y", float, 0, 0.45, 0.01),
            ("patch_width", "Ширина образца", int, 40, 500, 10),
            ("patch_height", "Высота образца", int, 40, 500, 10),
            ("background_sigma", "Нормализация фона", float, 0.1, 30, 0.5),
        ),
    ),
    (
        "OCR и группы текста",
        (
            ("ocr_psm", "Режим сегментации", int, 6, 13, 1),
            ("ocr_min_confidence", "Мин. уверенность OCR", float, 0, 100, 1),
            ("ocr_upscale", "Увеличение текста", int, 1, 5, 1),
            ("ocr_fallback_rotation", "Поворот при низкой увер.", int, -90, 180, 90),
            ("ocr_baseline_min_ratio", "Мин. длина линии", float, 0, 1, 0.05),
            ("ocr_consensus_min_votes", "Мин. совпадений OCR", int, 2, 3, 1),
            ("ocr_max_components", "Макс. фрагментов OCR", int, 1, 100, 1),
        ),
    ),
)


def make_preview_photo(image, max_width, max_height):
    height, width = image.shape[:2]
    scale = min(max_width / width, max_height / height, 1.0)
    display_width = max(1, int(round(width * scale)))
    display_height = max(1, int(round(height * scale)))
    if scale < 1.0:
        display_image = cv2.resize(
            image,
            (display_width, display_height),
            interpolation=cv2.INTER_AREA,
        )
    else:
        display_image = image

    success, encoded = cv2.imencode(".png", display_image)
    if not success:
        raise ValueError("Не удалось подготовить изображение для предпросмотра")
    image_data = base64.b64encode(encoded.tobytes()).decode("ascii")
    return tk.PhotoImage(data=image_data, format="png")


class CameraWindow:
    SERIES_COUNT = 3
    SERIES_INTERVAL_MS = 5000

    CAMERA_PROPERTIES = (
        ("frame_width", "Ширина", cv2.CAP_PROP_FRAME_WIDTH, 160, 3840, 10),
        ("frame_height", "Высота", cv2.CAP_PROP_FRAME_HEIGHT, 120, 2160, 10),
        ("fps", "FPS", cv2.CAP_PROP_FPS, 1, 240, 1),
        ("exposure", "Экспозиция", cv2.CAP_PROP_EXPOSURE, -20, 2048, 1),
        ("focus", "Фокус", cv2.CAP_PROP_FOCUS, 0, 255, 1),
        ("brightness", "Яркость", cv2.CAP_PROP_BRIGHTNESS, 0, 255, 1),
        ("contrast", "Контраст", cv2.CAP_PROP_CONTRAST, 0, 255, 1),
        ("saturation", "Насыщенность", cv2.CAP_PROP_SATURATION, 0, 255, 1),
        ("gain", "Усиление", cv2.CAP_PROP_GAIN, 0, 255, 1),
    )

    def __init__(self, parent, on_capture, camera_index=0):
        self.parent = parent
        self.on_capture = on_capture
        self.capture = None
        self.current_frame = None
        self.preview_photo = None
        self.update_job = None
        self.settings_job = None
        self.series_job = None
        self.series_paths = []
        self.camera_property_vars = {}
        self.auto_exposure_var = tk.BooleanVar(value=True)
        self.autofocus_var = tk.BooleanVar(value=True)

        self.window = tk.Toplevel(parent)
        self.window.title("Камера")
        screen_width = self.window.winfo_screenwidth()
        screen_height = self.window.winfo_screenheight()
        window_width = min(960, max(700, screen_width - 80))
        window_height = min(600, max(460, screen_height - 140))
        position_x = max(0, (screen_width - window_width) // 2)
        position_y = max(0, (screen_height - window_height) // 2 - 20)
        self.window.geometry(
            "{}x{}+{}+{}".format(
                window_width, window_height, position_x, position_y
            )
        )
        self.window.minsize(
            min(700, window_width), min(460, window_height)
        )
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())
        self.window.rowconfigure(1, weight=1)
        self.window.columnconfigure(0, weight=1)

        controls = ttk.Frame(self.window, padding=10)
        controls.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(controls, text="Индекс камеры:").pack(side="left")
        self.camera_index_var = tk.IntVar(value=camera_index)
        ttk.Spinbox(
            controls,
            from_=0,
            to=10,
            width=5,
            textvariable=self.camera_index_var,
        ).pack(side="left", padx=(6, 8))
        ttk.Button(
            controls, text="Подключить", command=self.open_camera
        ).pack(side="left")
        self.status_var = tk.StringVar(value="Подключение к камере...")
        ttk.Label(controls, textvariable=self.status_var).pack(
            side="left", padx=12
        )

        preview_frame = ttk.Frame(self.window, relief="sunken", borderwidth=1)
        preview_frame.grid(row=1, column=0, sticky="nsew", padx=10)
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            preview_frame, text="Нет изображения с камеры", anchor="center"
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")

        settings = ttk.LabelFrame(
            self.window, text="Параметры камеры", padding=10
        )
        settings.grid(row=1, column=1, sticky="ns", padx=(10, 10))
        settings.columnconfigure(1, weight=1)
        for row, (key, label, _, minimum, maximum, step) in enumerate(
            self.CAMERA_PROPERTIES
        ):
            ttk.Label(settings, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 8), pady=3
            )
            variable = tk.StringVar()
            self.camera_property_vars[key] = variable
            ttk.Spinbox(
                settings,
                from_=minimum,
                to=maximum,
                increment=step,
                width=11,
                textvariable=variable,
            ).grid(row=row, column=1, sticky="ew", pady=3)

        option_row = len(self.CAMERA_PROPERTIES)
        ttk.Checkbutton(
            settings,
            text="Автоэкспозиция",
            variable=self.auto_exposure_var,
        ).grid(row=option_row, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Checkbutton(
            settings,
            text="Автофокус",
            variable=self.autofocus_var,
        ).grid(row=option_row + 1, column=0, columnspan=2, sticky="w", pady=2)

        settings_actions = ttk.Frame(settings)
        settings_actions.grid(
            row=option_row + 2, column=0, columnspan=2, sticky="ew", pady=(10, 0)
        )
        settings_actions.columnconfigure((0, 1), weight=1)
        ttk.Button(
            settings_actions,
            text="Применить",
            command=self.apply_camera_settings,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(
            settings_actions,
            text="Считать",
            command=self.read_camera_settings,
        ).grid(row=0, column=1, sticky="ew", padx=(3, 0))

        ttk.Label(
            settings,
            text="Поддержка параметров зависит\nот камеры и драйвера.",
            foreground="#666666",
            justify="left",
        ).grid(
            row=option_row + 3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 0),
        )

        actions = ttk.Frame(self.window, padding=10)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew")
        actions.columnconfigure(0, weight=1)
        self.capture_button = ttk.Button(
            actions,
            text="Сделать и сохранить снимок",
            command=self.save_frame,
            state="disabled",
        )
        self.capture_button.grid(row=0, column=1, padx=(0, 8))
        self.series_button = ttk.Button(
            actions,
            text="Серия 3 снимка / 10 секунд",
            command=self.start_series_capture,
            state="disabled",
        )
        self.series_button.grid(row=0, column=2, padx=(0, 8))
        ttk.Button(actions, text="Закрыть", command=self.close).grid(
            row=0, column=3
        )

        self.window.grab_set()
        self.open_camera()

    def open_camera(self):
        self._release_camera()
        self.current_frame = None
        self.capture_button.configure(state="disabled")
        try:
            camera_index = int(self.camera_index_var.get())
        except (TypeError, ValueError):
            self.status_var.set("Некорректный индекс камеры")
            return

        if sys.platform.startswith("win"):
            self.capture = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not self.capture.isOpened():
                self.capture.release()
                self.capture = cv2.VideoCapture(camera_index)
        else:
            self.capture = cv2.VideoCapture(camera_index)

        if not self.capture.isOpened():
            self.status_var.set("Камера {} недоступна".format(camera_index))
            self._release_camera()
            return

        self.status_var.set("Камера {} подключена".format(camera_index))
        self.read_camera_settings()
        self._update_preview()

    @staticmethod
    def _format_camera_value(value):
        if abs(value - round(value)) < 0.001:
            return str(int(round(value)))
        return "{:.3f}".format(value).rstrip("0").rstrip(".")

    def read_camera_settings(self):
        if self.capture is None or not self.capture.isOpened():
            self.status_var.set("Сначала подключите камеру")
            return

        for key, _, property_id, _, _, _ in self.CAMERA_PROPERTIES:
            value = self.capture.get(property_id)
            self.camera_property_vars[key].set(
                self._format_camera_value(value)
            )

        auto_exposure = self.capture.get(cv2.CAP_PROP_AUTO_EXPOSURE)
        if sys.platform.startswith("win"):
            self.auto_exposure_var.set(auto_exposure >= 0.5)
        else:
            self.auto_exposure_var.set(auto_exposure >= 2)
        self.autofocus_var.set(
            self.capture.get(cv2.CAP_PROP_AUTOFOCUS) >= 0.5
        )

    def apply_camera_settings(self):
        if self.capture is None or not self.capture.isOpened():
            self.status_var.set("Сначала подключите камеру")
            return

        try:
            values = {
                key: float(self.camera_property_vars[key].get().replace(",", "."))
                for key, _, _, _, _, _ in self.CAMERA_PROPERTIES
            }
        except ValueError:
            messagebox.showerror(
                "Параметры камеры", "Все значения должны быть числами"
            )
            return

        auto_exposure_value = (
            0.75 if self.auto_exposure_var.get() else 0.25
        ) if sys.platform.startswith("win") else (
            3 if self.auto_exposure_var.get() else 1
        )
        attempts = [
            self.capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, auto_exposure_value),
            self.capture.set(
                cv2.CAP_PROP_AUTOFOCUS,
                1 if self.autofocus_var.get() else 0,
            ),
        ]
        for key, _, property_id, _, _, _ in self.CAMERA_PROPERTIES:
            if key == "exposure" and self.auto_exposure_var.get():
                continue
            if key == "focus" and self.autofocus_var.get():
                continue
            attempts.append(self.capture.set(property_id, values[key]))

        supported = sum(bool(result) for result in attempts)
        self.status_var.set(
            "Применено параметров: {}/{}".format(supported, len(attempts))
        )
        self.settings_job = self.window.after(
            250, self._read_settings_after_apply
        )

    def _read_settings_after_apply(self):
        self.settings_job = None
        if self.window.winfo_exists():
            self.read_camera_settings()

    def _update_preview(self):
        if self.capture is None or not self.capture.isOpened():
            return
        success, frame = self.capture.read()
        if success:
            self.current_frame = frame
            max_width = max(self.preview_label.winfo_width() - 16, 1)
            max_height = max(self.preview_label.winfo_height() - 16, 1)
            try:
                self.preview_photo = make_preview_photo(
                    frame, max_width, max_height
                )
                self.preview_label.configure(
                    image=self.preview_photo, text=""
                )
                self.capture_button.configure(state="normal")
                if self.series_job is None:
                    self.series_button.configure(state="normal")
            except ValueError as error:
                self.status_var.set(str(error))
        else:
            self.status_var.set("Не удалось получить кадр")
        self.update_job = self.window.after(33, self._update_preview)

    def save_frame(self):
        if self.current_frame is None:
            messagebox.showinfo("Камера", "Сначала дождитесь кадра с камеры")
            return

        image_path = self._save_current_frame("camera")
        if image_path is None:
            return
        self.close()
        self.on_capture(image_path)

    def _save_current_frame(self, prefix):
        if self.current_frame is None:
            return None

        image_dir = Path(__file__).resolve().parent / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        image_path = image_dir / "{}_{}.jpg".format(prefix, timestamp)
        if not cv2.imwrite(str(image_path), self.current_frame):
            messagebox.showerror("Ошибка", "Не удалось сохранить снимок")
            return None
        return image_path

    def start_series_capture(self):
        if self.current_frame is None or self.series_job is not None:
            return
        self.series_paths = []
        self.capture_button.configure(state="disabled")
        self.series_button.configure(state="disabled")
        self._capture_series_frame()

    def _capture_series_frame(self):
        self.series_job = None
        image_path = self._save_current_frame("camera_series")
        if image_path is None:
            self.capture_button.configure(state="normal")
            self.series_button.configure(state="normal")
            return
        self.series_paths.append(image_path)
        captured = len(self.series_paths)
        self.status_var.set(
            "Серия: снимок {}/{}".format(captured, self.SERIES_COUNT)
        )
        if captured < self.SERIES_COUNT:
            self.series_job = self.window.after(
                self.SERIES_INTERVAL_MS, self._capture_series_frame
            )
            return

        paths = list(self.series_paths)
        self.close()
        self.on_capture(paths)

    def _release_camera(self):
        if self.update_job:
            self.window.after_cancel(self.update_job)
            self.update_job = None
        if self.settings_job:
            self.window.after_cancel(self.settings_job)
            self.settings_job = None
        if self.series_job:
            self.window.after_cancel(self.series_job)
            self.series_job = None
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def close(self):
        self._release_camera()
        if self.window.winfo_exists():
            self.window.grab_release()
            self.window.destroy()


class CategoryEditor:
    def __init__(self, parent, categories, on_change):
        self.categories = categories
        self.on_change = on_change

        self.window = tk.Toplevel(parent)
        self.window.title("Редактор категорий")
        self.window.geometry("520x380")
        self.window.minsize(440, 320)
        self.window.transient(parent)
        self.window.grab_set()
        self.window.rowconfigure(0, weight=1)
        self.window.columnconfigure(0, weight=1)

        frame = ttk.Frame(self.window, padding=10)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.table = ttk.Treeview(
            frame, columns=("id", "name"), show="headings", selectmode="browse"
        )
        self.table.heading("id", text="Номер")
        self.table.heading("name", text="Название")
        self.table.column("id", width=80, anchor="center", stretch=False)
        self.table.column("name", width=330, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind("<Double-1>", lambda event: self.rename_category())

        actions = ttk.Frame(frame)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Добавить", command=self.add_category).pack(
            side="left"
        )
        ttk.Button(actions, text="Переименовать", command=self.rename_category).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="Удалить", command=self.delete_category).pack(
            side="left"
        )
        ttk.Button(actions, text="Закрыть", command=self.window.destroy).pack(
            side="right"
        )
        self.refresh()

    def refresh(self, select_id=None):
        for item in self.table.get_children():
            self.table.delete(item)
        selected_item = None
        for category_id, name in sorted(self.categories.items()):
            item = self.table.insert("", "end", values=(category_id, name))
            if category_id == select_id:
                selected_item = item
        if selected_item:
            self.table.selection_set(selected_item)
            self.table.focus(selected_item)

    def _selected_category_id(self):
        selection = self.table.selection()
        if not selection:
            messagebox.showinfo(
                "Категории", "Сначала выберите категорию", parent=self.window
            )
            return None
        return int(self.table.item(selection[0], "values")[0])

    def add_category(self):
        name = simpledialog.askstring(
            "Новая категория", "Название категории:", parent=self.window
        )
        if not name or not name.strip():
            return
        category_id = max(self.categories, default=0) + 1
        self.categories[category_id] = name.strip()
        self.on_change()
        self.refresh(category_id)

    def rename_category(self):
        category_id = self._selected_category_id()
        if category_id is None:
            return
        name = simpledialog.askstring(
            "Переименование",
            "Новое название категории:",
            initialvalue=self.categories[category_id],
            parent=self.window,
        )
        if not name or not name.strip():
            return
        self.categories[category_id] = name.strip()
        self.on_change()
        self.refresh(category_id)

    def delete_category(self):
        category_id = self._selected_category_id()
        if category_id is None:
            return
        if category_id in DEFAULT_CATEGORIES:
            messagebox.showwarning(
                "Категории",
                "Автоматическую категорию можно переименовать, но нельзя удалить",
                parent=self.window,
            )
            return
        if len(self.categories) == 1:
            messagebox.showwarning(
                "Категории",
                "Нельзя удалить последнюю категорию",
                parent=self.window,
            )
            return
        if not messagebox.askyesno(
            "Удаление",
            "Удалить категорию {} — {}?".format(
                category_id, self.categories[category_id]
            ),
            parent=self.window,
        ):
            return
        del self.categories[category_id]
        self.on_change(category_id)
        self.refresh()


class DetectorUI:
    def __init__(self, root, initial_image=None):
        self.root = root
        self.root.title("OpenCV Object Detector")
        self.root.geometry("1380x860")
        self.root.minsize(1050, 680)
        try:
            self.root.state("zoomed")
        except tk.TclError:
            self.root.attributes("-zoomed", True)

        self.image_path = None
        self.image_series = []
        self.images = {}
        self.detections = []
        self.preview_photo = None
        self.update_job = None
        self.processing = False
        self.categories = self._load_categories()
        self.category_overrides_by_image = {}
        self.text_group_registry = {}
        try:
            self.default_config = load_detector_config()
            config_load_error = None
        except ValueError as error:
            self.default_config = DetectorConfig()
            config_load_error = str(error)

        self.parameter_vars = {}
        self.ocr_enabled_var = tk.BooleanVar(value=True)
        self.ocr_console_debug_var = tk.BooleanVar(value=True)
        self.ocr_consensus_enabled_var = tk.BooleanVar(value=True)
        self.ocr_language_var = tk.StringVar(value="rus")
        self.ocr_expected_pattern_var = tk.StringVar(value="")
        self.view_var = tk.StringVar(value="result")
        self.auto_var = tk.BooleanVar(value=True)
        self.category_choice_var = tk.StringVar(value=AUTO_CATEGORY_LABEL)
        self.path_var = tk.StringVar(value="Изображение не выбрано")
        self.status_var = tk.StringVar(value="Выберите изображение")

        self._configure_style()
        self._build_layout()
        self._set_parameter_values(self.default_config)
        self._bind_parameter_updates()
        self._bind_shortcuts()
        if config_load_error:
            self.status_var.set("Ошибка файла параметров: {}".format(config_load_error))

        if initial_image:
            self.root.after(50, lambda: self.load_image(initial_image))
        else:
            newest = self._find_newest_image()
            if newest:
                self.root.after(50, lambda: self.load_image(newest))

    def _configure_style(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    @staticmethod
    def _load_categories():
        try:
            stored = json.loads(CATEGORY_FILE.read_text(encoding="utf-8"))
            categories = {
                int(category_id): str(name).strip()
                for category_id, name in stored.items()
                if str(name).strip()
            }
            return categories or dict(DEFAULT_CATEGORIES)
        except (OSError, ValueError, TypeError):
            return dict(DEFAULT_CATEGORIES)

    def _save_categories(self):
        serialized = {
            str(category_id): name
            for category_id, name in sorted(self.categories.items())
        }
        CATEGORY_FILE.write_text(
            json.dumps(serialized, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _category_choices(self):
        return [AUTO_CATEGORY_LABEL] + [
            "{} — {}".format(category_id, name)
            for category_id, name in sorted(self.categories.items())
        ]

    def _refresh_category_choices(self):
        choices = self._category_choices()
        self.category_combobox.configure(values=choices)
        if self.category_choice_var.get() not in choices:
            self.category_choice_var.set(AUTO_CATEGORY_LABEL)

    def _build_layout(self):
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, padding=(10, 10, 5, 10), width=340)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        ttk.Label(sidebar, text="Параметры детектора", style="Title.TLabel").pack(
            anchor="w", pady=(0, 8)
        )

        parameter_canvas = tk.Canvas(sidebar, highlightthickness=0, width=315)
        scrollbar = ttk.Scrollbar(
            sidebar, orient="vertical", command=parameter_canvas.yview
        )
        parameter_canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        parameter_canvas.pack(side="left", fill="both", expand=True)

        parameter_frame = ttk.Frame(parameter_canvas)
        canvas_window = parameter_canvas.create_window(
            (0, 0), window=parameter_frame, anchor="nw"
        )
        parameter_frame.bind(
            "<Configure>",
            lambda event: parameter_canvas.configure(
                scrollregion=parameter_canvas.bbox("all")
            ),
        )
        parameter_canvas.bind(
            "<Configure>",
            lambda event: parameter_canvas.itemconfigure(
                canvas_window, width=event.width
            ),
        )
        parameter_canvas.bind_all(
            "<MouseWheel>",
            lambda event: parameter_canvas.yview_scroll(
                int(-event.delta / 120), "units"
            ),
        )

        for group_name, fields in PARAMETER_GROUPS:
            group = ttk.LabelFrame(
                parameter_frame,
                text=group_name,
                style="Section.TLabelframe",
                padding=8,
            )
            group.pack(fill="x", pady=(0, 8))
            group.columnconfigure(1, weight=1)
            for row, (key, label, _, minimum, maximum, step) in enumerate(fields):
                ttk.Label(group, text=label).grid(
                    row=row, column=0, sticky="w", padx=(0, 8), pady=2
                )
                variable = tk.StringVar()
                self.parameter_vars[key] = variable
                spinbox = ttk.Spinbox(
                    group,
                    from_=minimum,
                    to=maximum,
                    increment=step,
                    textvariable=variable,
                    width=11,
                )
                spinbox.grid(row=row, column=1, sticky="ew", pady=2)

        ocr_options = ttk.LabelFrame(
            parameter_frame,
            text="OCR: включение и язык",
            style="Section.TLabelframe",
            padding=8,
        )
        ocr_options.pack(fill="x", pady=(0, 8))
        ocr_options.columnconfigure(1, weight=1)
        ttk.Checkbutton(
            ocr_options,
            text="Распознавать текст",
            variable=self.ocr_enabled_var,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            ocr_options,
            text="OCR-отладка в консоли",
            variable=self.ocr_console_debug_var,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            ocr_options,
            text="Повторная проверка сомнительного OCR",
            variable=self.ocr_consensus_enabled_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Label(ocr_options, text="Язык Tesseract").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=2
        )
        ttk.Entry(
            ocr_options,
            textvariable=self.ocr_language_var,
            width=11,
        ).grid(row=3, column=1, sticky="ew", pady=2)
        ttk.Label(ocr_options, text="Ожидаемый формат (regex)").grid(
            row=4, column=0, sticky="w", padx=(0, 8), pady=2
        )
        ttk.Entry(
            ocr_options,
            textvariable=self.ocr_expected_pattern_var,
            width=11,
        ).grid(row=4, column=1, sticky="ew", pady=2)

        actions = ttk.Frame(parameter_frame)
        actions.pack(fill="x", pady=(2, 8))
        actions.columnconfigure((0, 1), weight=1)
        ttk.Button(
            actions,
            text="Применить",
            style="Accent.TButton",
            command=self.process_current,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(actions, text="По умолчанию", command=self.reset_parameters).grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        ttk.Button(
            actions,
            text="Сохранить как параметры по умолчанию",
            command=self.save_default_parameters,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Checkbutton(
            parameter_frame,
            text="Автоматически применять изменения",
            variable=self.auto_var,
        ).pack(anchor="w", pady=(0, 10))

        main = ttk.Frame(self.root, padding=(5, 10, 10, 10))
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(main)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(2, weight=1)
        ttk.Button(toolbar, text="Открыть изображение", command=self.choose_image).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(toolbar, text="Камера", command=self.open_camera).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Label(toolbar, textvariable=self.path_var).grid(
            row=0, column=2, sticky="w"
        )
        ttk.Button(toolbar, text="Сохранить результат", command=self.save_result).grid(
            row=0, column=3, padx=(8, 4)
        )
        ttk.Button(toolbar, text="Сохранить отладку", command=self.save_debug).grid(
            row=0, column=4, padx=(4, 0)
        )

        view_bar = ttk.Frame(main)
        view_bar.grid(row=1, column=0, sticky="w", pady=(0, 6))
        ttk.Label(view_bar, text="Показывать:").pack(side="left", padx=(0, 6))
        for text, value in (
            ("Результат", "result"),
            ("Отладка", "debug"),
            ("Оригинал", "original"),
            ("Canny", "edges"),
            ("Морфология", "closed"),
            ("OCR-слой", "ocr"),
        ):
            ttk.Radiobutton(
                view_bar,
                text=text,
                value=value,
                variable=self.view_var,
                command=self.render_preview,
            ).pack(side="left", padx=3)

        preview_frame = ttk.Frame(main, relief="sunken", borderwidth=1)
        preview_frame.grid(row=2, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(
            preview_frame, text="Откройте изображение", anchor="center"
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_label.bind("<Configure>", lambda event: self.render_preview())

        table_frame = ttk.LabelFrame(main, text="Найденные объекты", padding=5)
        table_frame.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        table_frame.columnconfigure(0, weight=1)

        category_bar = ttk.Frame(table_frame)
        category_bar.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Button(
            category_bar,
            text="Редактор категорий",
            command=self.open_category_editor,
        ).pack(side="left")
        ttk.Label(category_bar, text="Назначить:").pack(
            side="left", padx=(12, 5)
        )
        self.category_combobox = ttk.Combobox(
            category_bar,
            textvariable=self.category_choice_var,
            values=self._category_choices(),
            state="readonly",
            width=28,
        )
        self.category_combobox.pack(side="left")
        ttk.Button(
            category_bar,
            text="Выбранным",
            command=self.assign_category_to_selected,
        ).pack(side="left", padx=(5, 3))
        ttk.Button(
            category_bar,
            text="Всему ID",
            command=self.assign_category_to_same_id,
        ).pack(side="left", padx=3)
        columns = (
            "object",
            "category",
            "id",
            "x",
            "y",
            "corners",
            "vertices",
            "content",
            "score",
            "text_group",
            "ocr_text",
            "ocr_confidence",
            "ocr_status",
        )
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=6
        )
        headings = {
            "object": "Объект",
            "category": "Категория",
            "id": "ID",
            "x": "X",
            "y": "Y",
            "corners": "Углы",
            "vertices": "Вершины",
            "content": "Содержимое",
            "score": "Mark score",
            "text_group": "Text ID",
            "ocr_text": "Текст OCR",
            "ocr_confidence": "OCR %",
            "ocr_status": "Статус OCR",
        }
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=90, anchor="center", stretch=True)
        self.table.grid(row=1, column=0, sticky="ew")
        table_scrollbar = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.table.xview
        )
        table_scrollbar.grid(row=2, column=0, sticky="ew")
        self.table.configure(xscrollcommand=table_scrollbar.set)

        ttk.Label(main, textvariable=self.status_var, anchor="w").grid(
            row=4, column=0, sticky="ew", pady=(6, 0)
        )

    def _bind_parameter_updates(self):
        for variable in self.parameter_vars.values():
            variable.trace_add("write", self._schedule_auto_update)
        self.ocr_enabled_var.trace_add("write", self._schedule_auto_update)
        self.ocr_console_debug_var.trace_add("write", self._schedule_auto_update)
        self.ocr_consensus_enabled_var.trace_add("write", self._schedule_auto_update)
        self.ocr_language_var.trace_add("write", self._schedule_auto_update)
        self.ocr_expected_pattern_var.trace_add("write", self._schedule_auto_update)

    def _bind_shortcuts(self):
        self.root.bind("<Control-o>", lambda event: self.choose_image())
        self.root.bind("<Control-k>", lambda event: self.open_camera())
        self.root.bind("<Control-s>", lambda event: self.save_result())
        self.root.bind("<F5>", lambda event: self.process_current())

    def _schedule_auto_update(self, *_):
        if not self.auto_var.get() or not self.image_path:
            return
        if self.update_job:
            self.root.after_cancel(self.update_job)
        self.update_job = self.root.after(450, lambda: self.process_current(False))

    def _set_parameter_values(self, config):
        values = asdict(config)
        for key, variable in self.parameter_vars.items():
            variable.set(str(values[key]))
        self.ocr_enabled_var.set(config.ocr_enabled)
        self.ocr_console_debug_var.set(config.ocr_console_debug)
        self.ocr_consensus_enabled_var.set(config.ocr_consensus_enabled)
        self.ocr_language_var.set(config.ocr_language)
        self.ocr_expected_pattern_var.set(config.ocr_expected_pattern)

    def _build_config(self):
        type_by_key = {
            key: value_type
            for _, fields in PARAMETER_GROUPS
            for key, _, value_type, _, _, _ in fields
        }
        values = {
            key: type_by_key[key](variable.get().replace(",", "."))
            for key, variable in self.parameter_vars.items()
        }
        values["ocr_enabled"] = self.ocr_enabled_var.get()
        values["ocr_console_debug"] = self.ocr_console_debug_var.get()
        values["ocr_consensus_enabled"] = self.ocr_consensus_enabled_var.get()
        values["ocr_language"] = self.ocr_language_var.get().strip()
        values["ocr_expected_pattern"] = self.ocr_expected_pattern_var.get().strip()
        return DetectorConfig(**values)

    def _find_newest_image(self):
        image_dir = Path(__file__).resolve().parent / "images"
        candidates = [
            path
            for path in image_dir.glob("*")
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        ]
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    def choose_image(self):
        path = filedialog.askopenfilename(
            title="Выберите изображение",
            filetypes=(
                ("Изображения", "*.png *.jpg *.jpeg *.bmp *.webp"),
                ("Все файлы", "*.*"),
            ),
        )
        if path:
            self.load_image(path)

    def open_camera(self, camera_index=0):
        CameraWindow(self.root, self.load_capture, camera_index)

    def open_category_editor(self):
        CategoryEditor(self.root, self.categories, self._categories_changed)

    def _categories_changed(self, deleted_category_id=None):
        if deleted_category_id is not None:
            for overrides in self.category_overrides_by_image.values():
                for object_number in [
                    number
                    for number, category_id in overrides.items()
                    if category_id == deleted_category_id
                ]:
                    del overrides[object_number]
        try:
            self._save_categories()
        except OSError as error:
            messagebox.showerror("Категории", str(error))
        self._refresh_category_choices()
        if self.image_path:
            self.process_current()

    def _current_image_key(self):
        return str(self.image_path.resolve()) if self.image_path else ""

    def _chosen_category_id(self):
        selected_index = self.category_combobox.current()
        if selected_index <= 0:
            return None
        category_ids = sorted(self.categories)
        category_position = selected_index - 1
        return (
            category_ids[category_position]
            if category_position < len(category_ids)
            else None
        )

    def _selected_object_numbers(self):
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo(
                "Категории", "Выберите один или несколько объектов в таблице"
            )
            return []
        return [int(self.table.item(item, "values")[0]) for item in selected]

    def _apply_category_assignment(self, object_numbers):
        if not object_numbers or not self.image_path:
            return
        category_id = self._chosen_category_id()
        overrides = self.category_overrides_by_image.setdefault(
            self._current_image_key(), {}
        )
        for object_number in object_numbers:
            if category_id is None:
                overrides.pop(object_number, None)
            else:
                overrides[object_number] = category_id
        self.process_current()

    def assign_category_to_selected(self):
        self._apply_category_assignment(self._selected_object_numbers())

    def assign_category_to_same_id(self):
        selected_objects = self._selected_object_numbers()
        if not selected_objects:
            return
        selected_ids = {
            detection["id"]
            for detection in self.detections
            if detection["object"] in selected_objects
        }
        object_numbers = [
            detection["object"]
            for detection in self.detections
            if detection["id"] in selected_ids
        ]
        self._apply_category_assignment(object_numbers)

    def load_image(self, path):
        self.image_series = []
        self.image_path = Path(path)
        self.path_var.set(str(self.image_path))
        self.process_current()

    def load_capture(self, capture):
        if isinstance(capture, (list, tuple)):
            self.image_series = [Path(path) for path in capture]
            self.image_path = self.image_series[-1]
            self.path_var.set(
                "Серия: {}".format(
                    ", ".join(path.name for path in self.image_series)
                )
            )
            self.process_current()
        else:
            self.load_image(capture)

    def reset_parameters(self):
        try:
            config = load_detector_config()
        except ValueError as error:
            messagebox.showerror("Параметры по умолчанию", str(error))
            return
        auto_enabled = self.auto_var.get()
        self.auto_var.set(False)
        self._set_parameter_values(config)
        self.auto_var.set(auto_enabled)
        self.default_config = config
        if self.image_path:
            self.process_current()

    def save_default_parameters(self):
        try:
            config = self._build_config()
            path = save_detector_config(config)
        except (OSError, ValueError) as error:
            messagebox.showerror("Параметры по умолчанию", str(error))
            return
        self.default_config = config
        self.status_var.set("Параметры по умолчанию сохранены: {}".format(path))
        messagebox.showinfo(
            "Параметры по умолчанию",
            "Текущие параметры сохранены в detector_config.json",
        )

    def _select_series_result(self, outputs):
        """Choose a representative frame and apply 2-of-3 OCR voting."""
        match_radius = 90.0
        tracks = []
        detection_tracks = {}
        for frame_index, output in enumerate(outputs):
            used_tracks = set()
            for detection in output[4]:
                center_x, center_y = detection["center"]
                choices = []
                for track_index, track in enumerate(tracks):
                    if track_index in used_tracks:
                        continue
                    distance = (
                        (center_x - track["center"][0]) ** 2
                        + (center_y - track["center"][1]) ** 2
                    ) ** 0.5
                    if distance <= match_radius:
                        choices.append((distance, track_index))
                if choices:
                    _, track_index = min(choices)
                    track = tracks[track_index]
                else:
                    track_index = len(tracks)
                    track = {"center": (center_x, center_y), "items": []}
                    tracks.append(track)
                track["items"].append((frame_index, detection))
                mean_x = sum(item[1]["center"][0] for item in track["items"]) / len(
                    track["items"]
                )
                mean_y = sum(item[1]["center"][1] for item in track["items"]) / len(
                    track["items"]
                )
                track["center"] = (mean_x, mean_y)
                used_tracks.add(track_index)
                detection_tracks[id(detection)] = track_index

        winners = {}
        for track_index, track in enumerate(tracks):
            text_votes = {}
            for frame_index, detection in track["items"]:
                if detection["ocr_status"] == "recognized":
                    text_votes.setdefault(detection["ocr_text"], []).append(
                        (frame_index, detection)
                    )
            if text_votes:
                text, matches = max(
                    text_votes.items(),
                    key=lambda item: (
                        len(item[1]),
                        sum(match[1]["ocr_confidence"] for match in item[1]),
                    ),
                )
                if len(matches) >= 2:
                    winners[track_index] = text, matches

        def representative_score(frame_index):
            score = 0.0
            for detection in outputs[frame_index][4]:
                winner = winners.get(detection_tracks[id(detection)])
                if winner and detection["ocr_text"] == winner[0]:
                    score += 1000.0 + detection["ocr_confidence"]
            return score

        representative_index = max(
            range(len(outputs)), key=representative_score
        )
        selected = outputs[representative_index]
        detections = selected[4]

        for detection in detections:
            winner = winners.get(detection_tracks[id(detection)])
            if winner:
                text, matches = winner
                source = max(
                    (match[1] for match in matches),
                    key=lambda item: item["ocr_confidence"],
                )
                detection.update(
                    {
                        "ocr_text": text,
                        "ocr_raw_text": source["ocr_raw_text"],
                        "ocr_confidence": sum(
                            match[1]["ocr_confidence"] for match in matches
                        )
                        / len(matches),
                        "ocr_status": "recognized",
                        "ocr_rotation": source["ocr_rotation"],
                        "ocr_orientation_source": "{}+series({}/{})".format(
                            source["ocr_orientation_source"],
                            len(matches),
                            len(outputs),
                        ),
                        "ocr_word_boxes": source["ocr_word_boxes"],
                        "ocr_rotation_scores": source["ocr_rotation_scores"],
                    }
                )
            elif detection["ocr_status"] == "recognized":
                detection["ocr_status"] = "series_no_majority"
                detection["ocr_orientation_source"] += "+series(no_majority)"

            if detection["ocr_status"] == "recognized" and detection["ocr_text"]:
                text = detection["ocr_text"]
                if text not in self.text_group_registry:
                    self.text_group_registry[text] = len(self.text_group_registry) + 1
                detection["text_group"] = self.text_group_registry[text]
            else:
                detection["text_group"] = None
        return selected, len(winners)

    def process_current(self, show_errors=True):
        if not self.image_path or self.processing:
            return
        self.processing = True
        self.status_var.set("Обработка...")
        self.root.update_idletasks()
        try:
            config = self._build_config()
            paths = self.image_series or [self.image_path]
            outputs = []
            for path in paths:
                path_key = str(Path(path).resolve())
                outputs.append(
                    process_image(
                        path,
                        config=config,
                        category_names=self.categories,
                        category_overrides=self.category_overrides_by_image.get(
                            path_key, {}
                        ),
                        text_group_registry=(
                            {} if self.image_series else self.text_group_registry
                        ),
                    )
                )
            series_groups = 0
            if self.image_series:
                selected, series_groups = self._select_series_result(outputs)
            else:
                selected = outputs[0]
            image, raw_edges, closed_edges, result, detections = selected
            debug = create_debug_view(
                image,
                raw_edges,
                closed_edges,
                result,
                len(detections),
                config,
                detections,
            )
            ocr_debug = create_ocr_debug_view(image, detections, config)
            self.images = {
                "original": image,
                "edges": raw_edges,
                "closed": closed_edges,
                "result": result,
                "debug": debug,
                "ocr": ocr_debug,
            }
            self.detections = detections
            self._fill_table()
            self.render_preview()
            status = "Готово: найдено объектов — {}; текстовых групп — {}".format(
                    len(detections),
                    len(
                        {
                            detection["text_group"]
                            for detection in detections
                            if detection["text_group"] is not None
                        }
                    )
                )
            if self.image_series:
                status += "; серия {} кадров, большинство для {} объектов".format(
                    len(self.image_series), series_groups
                )
            self.status_var.set(status)
            ocr_problem = next(
                (
                    detection["ocr_status"]
                    for detection in detections
                    if detection["ocr_status"]
                    in {"unavailable", "language_missing", "error"}
                ),
                None,
            )
            if ocr_problem:
                self.status_var.set(
                    "Объекты найдены, но OCR недоступен: {}".format(ocr_problem)
                )
        except (ValueError, OSError) as error:
            self.status_var.set("Ошибка: {}".format(error))
            if show_errors:
                messagebox.showerror("Ошибка обработки", str(error))
        finally:
            self.processing = False

    def _fill_table(self):
        for item in self.table.get_children():
            self.table.delete(item)
        for detection in self.detections:
            center_x, center_y = detection["center"]
            self.table.insert(
                "",
                "end",
                values=(
                    detection["object"],
                    "{} — {}".format(
                        detection["category"], detection["category_name"]
                    ),
                    detection["id"],
                    center_x,
                    center_y,
                    detection["corner_style"],
                    detection["corner_vertices"],
                    detection["content"],
                    "{:.4f}".format(detection["mark_score"]),
                    detection["text_group"] or "—",
                    detection["ocr_text"] or "—",
                    "{:.1f}".format(detection["ocr_confidence"]),
                    detection["ocr_status"],
                ),
            )

    def render_preview(self):
        image = self.images.get(self.view_var.get())
        if image is None:
            return
        max_width = max(self.preview_label.winfo_width() - 16, 100)
        max_height = max(self.preview_label.winfo_height() - 16, 100)
        try:
            self.preview_photo = make_preview_photo(
                image, max_width, max_height
            )
        except ValueError as error:
            self.status_var.set(str(error))
            return
        self.preview_label.configure(image=self.preview_photo, text="")

    def _save_image(self, image_key, title, default_name):
        image = self.images.get(image_key)
        if image is None:
            messagebox.showinfo("Нет результата", "Сначала обработайте изображение")
            return
        path = filedialog.asksaveasfilename(
            title=title,
            defaultextension=".png",
            initialfile=default_name,
            filetypes=(("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg")),
        )
        if not path:
            return
        if not cv2.imwrite(path, image):
            messagebox.showerror("Ошибка", "Не удалось сохранить файл")
            return
        self.status_var.set("Сохранено: {}".format(path))

    def save_result(self):
        self._save_image("result", "Сохранить результат", "result.png")

    def save_debug(self):
        self._save_image("debug", "Сохранить отладку", "debug.png")


def parse_arguments():
    parser = argparse.ArgumentParser(description="OpenCV Object Detector UI")
    parser.add_argument("--image", help="optional image to open on startup")
    parser.add_argument(
        "--camera",
        type=int,
        metavar="INDEX",
        help="open the camera window on startup",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()
    root = tk.Tk()
    application = DetectorUI(root, args.image)
    if args.camera is not None:
        root.after(100, lambda: application.open_camera(args.camera))
    root.mainloop()


if __name__ == "__main__":
    main()
