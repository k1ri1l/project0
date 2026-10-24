from dataclasses import asdict, dataclass, fields, replace
from functools import lru_cache
import json
from pathlib import Path
import re
import shutil
import unicodedata

import cv2
import imutils
import numpy as np
from imutils import contours, perspective
from PIL import Image, ImageDraw, ImageFont

try:
    import pytesseract
    from pytesseract import Output
except ImportError:
    pytesseract = None
    Output = None


CONFIG_FILE = Path(__file__).resolve().parent / "detector_config.json"


@dataclass
class DetectorConfig:
    target_width: int = 640
    target_height: int = 480
    blur_kernel: int = 7
    canny_low: int = 50
    canny_high: int = 100
    morph_iterations: int = 1
    min_area: float = 500
    similarity_threshold: float = 0.05
    corner_epsilon: float = 0.01
    rectangularity_threshold: float = 0.85
    sharp_max_vertices: int = 4
    rounded_max_vertices: int = 12
    signed_threshold: float = 0.015
    detail_threshold: int = 16
    min_detail_area: int = 3
    interior_margin_x: float = 0.125
    interior_margin_y: float = 0.15
    patch_width: int = 120
    patch_height: int = 200
    background_sigma: float = 7.0
    ocr_enabled: bool = True
    ocr_language: str = "rus"
    ocr_psm: int = 7
    ocr_min_confidence: float = 45.0
    ocr_upscale: int = 3
    ocr_fallback_rotation: int = -90
    ocr_baseline_min_ratio: float = 0.35
    ocr_consensus_enabled: bool = True
    ocr_consensus_min_votes: int = 2
    ocr_max_components: int = 12
    ocr_expected_pattern: str = ""
    ocr_console_debug: bool = True


def validate_config(config):
    if config.target_width <= 0 or config.target_height <= 0:
        raise ValueError("Target width and height must be positive")
    if config.blur_kernel <= 0 or config.blur_kernel % 2 == 0:
        raise ValueError("Blur kernel must be a positive odd number")
    if not 0 <= config.canny_low <= 255 or not 0 <= config.canny_high <= 255:
        raise ValueError("Canny thresholds must be between 0 and 255")
    if config.canny_low >= config.canny_high:
        raise ValueError("Canny low threshold must be below the high threshold")
    if config.morph_iterations < 0:
        raise ValueError("Morphology iterations cannot be negative")
    if config.min_area < 0 or config.similarity_threshold < 0:
        raise ValueError("Area and similarity thresholds cannot be negative")
    if not 0 < config.corner_epsilon < 1:
        raise ValueError("Corner epsilon must be between 0 and 1")
    if not 0 <= config.rectangularity_threshold <= 1:
        raise ValueError("Rectangularity threshold must be between 0 and 1")
    if config.sharp_max_vertices < 3:
        raise ValueError("Sharp corner vertex limit must be at least 3")
    if config.rounded_max_vertices < config.sharp_max_vertices:
        raise ValueError("Rounded vertex limit must not be below sharp limit")
    if not 0 <= config.signed_threshold <= 1:
        raise ValueError("Signed threshold must be between 0 and 1")
    if not 0 <= config.detail_threshold <= 255:
        raise ValueError("Detail threshold must be between 0 and 255")
    if config.min_detail_area < 1:
        raise ValueError("Minimum detail area must be positive")
    if not 0 <= config.interior_margin_x < 0.5:
        raise ValueError("Horizontal interior margin must be below 0.5")
    if not 0 <= config.interior_margin_y < 0.5:
        raise ValueError("Vertical interior margin must be below 0.5")
    if config.patch_width <= 0 or config.patch_height <= 0:
        raise ValueError("Patch width and height must be positive")
    if config.background_sigma <= 0:
        raise ValueError("Background blur sigma must be positive")
    if not config.ocr_language.strip():
        raise ValueError("OCR language cannot be empty")
    if not 0 <= config.ocr_psm <= 13:
        raise ValueError("OCR page segmentation mode must be between 0 and 13")
    if not 0 <= config.ocr_min_confidence <= 100:
        raise ValueError("OCR confidence must be between 0 and 100")
    if config.ocr_upscale < 1:
        raise ValueError("OCR upscale must be at least 1")
    if config.ocr_fallback_rotation not in (-90, 0, 90, 180):
        raise ValueError("OCR fallback rotation must be -90, 0, 90, or 180")
    if not 0 <= config.ocr_baseline_min_ratio <= 1:
        raise ValueError("OCR baseline ratio must be between 0 and 1")
    if not 2 <= config.ocr_consensus_min_votes <= 3:
        raise ValueError("OCR consensus votes must be between 2 and 3")
    if config.ocr_max_components < 1:
        raise ValueError("OCR component limit must be positive")
    try:
        re.compile(config.ocr_expected_pattern)
    except re.error as error:
        raise ValueError("Invalid OCR expected pattern: {}".format(error)) from error


def load_detector_config(path=None):
    config_path = Path(path) if path is not None else CONFIG_FILE
    if not config_path.exists():
        return DetectorConfig()
    try:
        stored = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(
            "Unable to load detector settings from {}: {}".format(
                config_path, error
            )
        ) from error
    if not isinstance(stored, dict):
        raise ValueError("Detector settings must be a JSON object")

    allowed = {field.name for field in fields(DetectorConfig)}
    unknown = sorted(set(stored) - allowed)
    if unknown:
        raise ValueError(
            "Unknown detector settings: {}".format(", ".join(unknown))
        )
    try:
        config = DetectorConfig(**stored)
        validate_config(config)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Invalid detector settings in {}: {}".format(config_path, error)
        ) from error
    return config


def save_detector_config(config, path=None):
    validate_config(config)
    config_path = Path(path) if path is not None else CONFIG_FILE
    config_path.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path


def load_image(image_path, config=None):
    config = config or load_detector_config()
    validate_config(config)
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError("Unable to open image: {}".format(image_path))
    return cv2.resize(image, (config.target_width, config.target_height))


def create_edge_maps(image, config=None):
    config = config or load_detector_config()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (config.blur_kernel, config.blur_kernel), 0)
    raw_edges = cv2.Canny(gray, config.canny_low, config.canny_high)
    closed_edges = cv2.dilate(
        raw_edges, None, iterations=config.morph_iterations
    )
    closed_edges = cv2.erode(
        closed_edges, None, iterations=config.morph_iterations
    )
    return raw_edges, closed_edges


def find_objects(edge_map):
    found = cv2.findContours(
        edge_map.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    return imutils.grab_contours(found)


def midpoint(point_a, point_b):
    return (
        (point_a[0] + point_b[0]) * 0.5,
        (point_a[1] + point_b[1]) * 0.5,
    )


def remove_nested_contours(found_contours):
    outer_contours = []
    for contour in sorted(found_contours, key=cv2.contourArea, reverse=True):
        center = cv2.minAreaRect(contour)[0]
        is_nested = any(
            cv2.pointPolygonTest(
                cv2.boxPoints(cv2.minAreaRect(outer_contour)), center, False
            )
            >= 0
            for outer_contour in outer_contours
        )
        if not is_nested:
            outer_contours.append(contour)
    return outer_contours


def describe_corners(contour, config):
    perimeter = cv2.arcLength(contour, True)
    approximated = cv2.approxPolyDP(
        contour, config.corner_epsilon * perimeter, True
    )
    vertex_count = len(approximated)

    rectangle = cv2.minAreaRect(contour)
    rectangle_area = rectangle[1][0] * rectangle[1][1]
    if rectangle_area <= 0:
        return "other", vertex_count

    rectangularity = cv2.contourArea(contour) / rectangle_area
    if rectangularity >= config.rectangularity_threshold:
        if vertex_count <= config.sharp_max_vertices:
            return "sharp", vertex_count
        if vertex_count <= config.rounded_max_vertices:
            return "rounded", vertex_count

    return "other", vertex_count


def assign_class_id(contour, object_classes, config):
    corner_style, vertex_count = describe_corners(contour, config)
    for class_index, class_contours in enumerate(object_classes):
        compatible_samples = [
            sample
            for sample in class_contours
            if describe_corners(sample, config)[0] == corner_style
        ]
        if not compatible_samples:
            continue

        best_score = min(
            cv2.matchShapes(contour, sample, cv2.CONTOURS_MATCH_I1, 0.0)
            for sample in compatible_samples
        )
        if best_score <= config.similarity_threshold:
            class_contours.append(contour)
            return class_index + 1, best_score, corner_style, vertex_count

    object_classes.append([contour])
    return len(object_classes), 0.0, corner_style, vertex_count


def extract_aligned_patch(image, box, output_size=(120, 200)):
    top_left, top_right, bottom_right, bottom_left = box.astype("float32")
    width = max(
        int(round(np.linalg.norm(bottom_right - bottom_left))),
        int(round(np.linalg.norm(top_right - top_left))),
        1,
    )
    height = max(
        int(round(np.linalg.norm(top_right - bottom_right))),
        int(round(np.linalg.norm(top_left - bottom_left))),
        1,
    )
    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype="float32",
    )
    transform = cv2.getPerspectiveTransform(box.astype("float32"), destination)
    patch = cv2.warpPerspective(image, transform, (width, height))
    if patch.shape[1] > patch.shape[0]:
        patch = cv2.rotate(patch, cv2.ROTATE_90_CLOCKWISE)
    return cv2.resize(patch, output_size)


def detect_internal_marking(image, box, config):
    patch = extract_aligned_patch(
        image, box, (config.patch_width, config.patch_height)
    )
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)

    height, width = gray.shape
    margin_y = int(round(height * config.interior_margin_y))
    margin_x = int(round(width * config.interior_margin_x))
    interior = gray[margin_y : height - margin_y, margin_x : width - margin_x]

    local_background = cv2.GaussianBlur(
        interior, (0, 0), config.background_sigma
    )
    dark_details = cv2.subtract(local_background, interior)
    binary_details = np.where(
        dark_details > config.detail_threshold, 255, 0
    ).astype("uint8")

    component_count, _, component_stats, _ = cv2.connectedComponentsWithStats(
        binary_details, 8
    )
    marked_pixels = sum(
        int(component_stats[index, cv2.CC_STAT_AREA])
        for index in range(1, component_count)
        if component_stats[index, cv2.CC_STAT_AREA] >= config.min_detail_area
    )
    mark_score = marked_pixels / float(binary_details.size)
    content = "signed" if mark_score >= config.signed_threshold else "blank"
    return content, mark_score


def normalize_ocr_text(text):
    """Return a strict key used to group equal OCR results."""
    normalized = unicodedata.normalize("NFKC", text).upper()
    normalized = normalized.replace("Ё", "Е")
    normalized = re.sub(r"[‐‑‒–—−]", "-", normalized)
    normalized = re.sub(r"[^0-9A-ZА-Я-]", "", normalized)
    # Tesseract often confuses zero with Latin/Cyrillic O in short part codes.
    normalized = re.sub(r"(?<=[A-ZА-Я])[OО](?=\d)", "0", normalized)
    normalized = re.sub(r"(?<=\d)[OО](?=\d)", "0", normalized)
    return normalized


@lru_cache(maxsize=1)
def get_ocr_availability():
    if pytesseract is None:
        return False, "Python package pytesseract is not installed"

    executable = shutil.which("tesseract")
    if executable is None:
        windows_candidate = Path(
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        )
        if windows_candidate.exists():
            executable = str(windows_candidate)
    if executable is None:
        return False, "Tesseract executable was not found"

    pytesseract.pytesseract.tesseract_cmd = executable
    try:
        languages = pytesseract.get_languages(config="")
    except (OSError, pytesseract.TesseractError) as error:
        return False, str(error)
    return True, languages


def _prepare_ocr_patch(image, box, config):
    patch = extract_aligned_patch(
        image, box, (config.patch_width, config.patch_height)
    )
    height, width = patch.shape[:2]
    margin_y = int(round(height * config.interior_margin_y))
    margin_x = int(round(width * config.interior_margin_x))
    interior = patch[
        margin_y : height - margin_y,
        margin_x : width - margin_x,
    ]
    gray = cv2.cvtColor(interior, cv2.COLOR_BGR2GRAY)
    if config.ocr_upscale > 1:
        gray = cv2.resize(
            gray,
            None,
            fx=config.ocr_upscale,
            fy=config.ocr_upscale,
            interpolation=cv2.INTER_CUBIC,
        )
    local_background = cv2.GaussianBlur(
        gray,
        (0, 0),
        max(config.background_sigma * config.ocr_upscale, 3.0),
    )
    normalized_background = cv2.divide(gray, local_background, scale=255)
    _, ocr_layer = cv2.threshold(
        normalized_background,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    return cv2.copyMakeBorder(
        ocr_layer, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255
    )


def _count_ocr_components(ocr_layer):
    foreground = (ocr_layer < 128).astype("uint8")
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(
        foreground, connectivity=8
    )
    if component_count <= 1:
        return 0
    minimum_area = max(8, int(round(ocr_layer.size * 0.00005)))
    areas = stats[1:, cv2.CC_STAT_AREA]
    return int(np.count_nonzero(areas >= minimum_area))


def _baseline_score(candidate, min_ratio):
    """Return the longest continuous dark run below the text as a ratio."""
    dark = candidate < 128
    best_ratio = 0.0
    for row_index, row in enumerate(dark):
        if row_index / float(candidate.shape[0]) < 0.55:
            continue
        padded = np.pad(row.astype("int8"), (1, 1))
        changes = np.diff(padded)
        starts = np.flatnonzero(changes == 1)
        ends = np.flatnonzero(changes == -1)
        if starts.size:
            run_ratio = float(np.max(ends - starts)) / candidate.shape[1]
            best_ratio = max(best_ratio, run_ratio)
    return best_ratio if best_ratio >= min_ratio else 0.0


def _choose_baseline_rotation(candidates, min_ratio):
    scored = [
        (_baseline_score(candidate, min_ratio), rotation)
        for rotation, candidate in candidates
    ]
    score, rotation = max(scored)
    return rotation if score > 0 else None


def _ocr_layout_score(candidate):
    """Estimate how closely foreground components resemble one text line."""
    foreground = (candidate < 128).astype("uint8")
    count, _, stats, centroids = cv2.connectedComponentsWithStats(
        foreground, connectivity=8
    )
    if count <= 1:
        return 0.0
    minimum_area = max(8, int(round(candidate.size * 0.00005)))
    keep = stats[1:, cv2.CC_STAT_AREA] >= minimum_area
    stats = stats[1:][keep]
    centroids = centroids[1:][keep]
    if not len(stats):
        return 0.0

    # Underlines help orientation, but should not distort the letter alignment.
    widths = stats[:, cv2.CC_STAT_WIDTH]
    heights = stats[:, cv2.CC_STAT_HEIGHT]
    text_like = ~(
        (widths >= candidate.shape[1] * 0.30)
        & (widths >= heights * 4)
    )
    if np.any(text_like):
        stats = stats[text_like]
        centroids = centroids[text_like]
        heights = heights[text_like]
    component_count = len(stats)
    if component_count == 0 or component_count > 20:
        return 0.0

    median_height = max(float(np.median(heights)), 1.0)
    vertical_spread = float(np.std(centroids[:, 1]))
    alignment = max(0.0, 1.0 - vertical_spread / (median_height * 0.75))
    count_score = min(component_count, 12) / 12.0
    left = np.min(stats[:, cv2.CC_STAT_LEFT])
    right = np.max(stats[:, cv2.CC_STAT_LEFT] + stats[:, cv2.CC_STAT_WIDTH])
    span_score = min(1.0, float(right - left) / (candidate.shape[1] * 0.35))
    return 100.0 * (0.60 * alignment + 0.20 * count_score + 0.20 * span_score)


def _read_ocr_candidate(candidate, language, psm):
    data = pytesseract.image_to_data(
        candidate,
        lang=language,
        config="--oem 1 --psm {}".format(psm),
        output_type=Output.DICT,
    )
    words = []
    confidences = []
    word_boxes = []
    for index, (raw_text, raw_confidence) in enumerate(
        zip(data["text"], data["conf"])
    ):
        text = str(raw_text).strip()
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if text and confidence >= 0:
            words.append(text)
            confidences.append(confidence)
            word_boxes.append(
                {
                    "text": text,
                    "confidence": confidence,
                    "box": [
                        int(data["left"][index]),
                        int(data["top"][index]),
                        int(data["width"][index]),
                        int(data["height"][index]),
                    ],
                }
            )

    raw_text = " ".join(words)
    normalized = normalize_ocr_text(raw_text)
    if not any(character.isalnum() for character in normalized):
        return "", "", 0.0, []
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return raw_text, normalized, confidence, word_boxes


def _apply_ocr_consensus(candidate, config, initial_result):
    """Confirm a weak OCR result by agreement between segmentation modes."""
    psms = tuple(dict.fromkeys((config.ocr_psm, 7, 11, 12)))
    results = [initial_result]
    for psm in psms:
        if psm != config.ocr_psm:
            results.append(
                _read_ocr_candidate(candidate, config.ocr_language, psm)
            )

    votes = {}
    for raw_text, normalized, confidence, word_boxes in results:
        if normalized:
            votes.setdefault(normalized, []).append(
                (raw_text, confidence, word_boxes)
            )
    if not votes:
        return initial_result, 0

    winner, matches = max(
        votes.items(),
        key=lambda item: (len(item[1]), max(value[1] for value in item[1])),
    )
    if len(matches) < config.ocr_consensus_min_votes:
        return initial_result, len(matches)

    best_raw, tesseract_confidence, best_boxes = max(
        matches, key=lambda value: value[1]
    )
    agreement_confidence = 100.0 * len(matches) / len(results)
    return (
        best_raw,
        winner,
        max(tesseract_confidence, agreement_confidence),
        best_boxes,
    ), len(matches)


def recognize_internal_text(image, box, config):
    if not config.ocr_enabled:
        return "", "", 0.0, "disabled", 0, "disabled", [], {}

    available, details = get_ocr_availability()
    if not available:
        return "", "", 0.0, "unavailable", 0, "unavailable", [], {}
    if config.ocr_language not in details:
        return "", "", 0.0, "language_missing", 0, "language_missing", [], {}

    prepared = _prepare_ocr_patch(image, box, config)
    candidates = (
        (0, prepared),
        (90, cv2.rotate(prepared, cv2.ROTATE_90_CLOCKWISE)),
        (-90, cv2.rotate(prepared, cv2.ROTATE_90_COUNTERCLOCKWISE)),
        (180, cv2.rotate(prepared, cv2.ROTATE_180)),
    )
    baseline_rotation = _choose_baseline_rotation(
        candidates, config.ocr_baseline_min_ratio
    )
    component_count = _count_ocr_components(prepared)
    if component_count > config.ocr_max_components:
        candidate_rotations = {rotation for rotation, _ in candidates}
        rejected_rotation = (
            baseline_rotation
            if baseline_rotation is not None
            else config.ocr_fallback_rotation
        )
        if rejected_rotation not in candidate_rotations:
            rejected_rotation = candidates[0][0]
        if config.ocr_console_debug:
            print(
                "[OCR quality] noise rejected: components={} limit={}".format(
                    component_count, config.ocr_max_components
                ),
                flush=True,
            )
        return (
            "",
            "",
            0.0,
            "noise_rejected",
            rejected_rotation,
            "noise_filter",
            [],
            {},
        )

    expected_pattern = (
        re.compile(config.ocr_expected_pattern)
        if config.ocr_expected_pattern
        else None
    )
    candidate_results = {}
    rotation_scores = {}
    try:
        for rotation, candidate in candidates:
            raw_text, normalized, confidence, word_boxes = _read_ocr_candidate(
                candidate, config.ocr_language, config.ocr_psm
            )
            layout_score = _ocr_layout_score(candidate)
            baseline_score = _baseline_score(
                candidate, config.ocr_baseline_min_ratio
            )
            format_match = bool(
                normalized
                and expected_pattern is not None
                and expected_pattern.fullmatch(normalized)
            )
            vertical_line_penalty = (
                30.0
                if config.ocr_psm in (7, 8, 13)
                and candidate.shape[1] < candidate.shape[0]
                else 0.0
            )
            total_score = min(
                100.0,
                max(
                    0.0,
                    0.65 * confidence
                    + 0.20 * layout_score
                    + 15.0 * baseline_score
                    + (20.0 if format_match else 0.0)
                    - vertical_line_penalty,
                ),
            )
            candidate_results[rotation] = (
                raw_text,
                normalized,
                confidence,
                word_boxes,
            )
            rotation_scores[rotation] = {
                "total": round(total_score, 1),
                "confidence": round(confidence, 1),
                "layout": round(layout_score, 1),
                "baseline": round(baseline_score, 3),
                "format": format_match,
                "vertical_penalty": vertical_line_penalty,
                "text": normalized,
            }
    except (OSError, pytesseract.TesseractError):
        return "", "", 0.0, "error", 0, "error", [], rotation_scores

    best_rotation = max(
        rotation_scores, key=lambda rotation: rotation_scores[rotation]["total"]
    )
    best_raw_text, best_text, best_confidence, best_word_boxes = candidate_results[
        best_rotation
    ]
    best_metrics = rotation_scores[best_rotation]
    if best_metrics["baseline"] > 0:
        orientation_source = "baseline"
    elif best_metrics["format"]:
        orientation_source = "format"
    else:
        orientation_source = "confidence"

    if (
        best_confidence < config.ocr_min_confidence
        and best_metrics["baseline"] <= 0
        and not best_metrics["format"]
        and config.ocr_fallback_rotation in candidate_results
    ):
        best_rotation = config.ocr_fallback_rotation
        best_raw_text, best_text, best_confidence, best_word_boxes = candidate_results[
            best_rotation
        ]
        orientation_source = "fallback"

    if config.ocr_consensus_enabled and best_confidence < config.ocr_min_confidence:
        selected_candidate = dict(candidates)[best_rotation]
        consensus_result, consensus_votes = _apply_ocr_consensus(
            selected_candidate,
            config,
            (best_raw_text, best_text, best_confidence, best_word_boxes),
        )
        if consensus_votes >= config.ocr_consensus_min_votes:
            (
                best_raw_text,
                best_text,
                best_confidence,
                best_word_boxes,
            ) = consensus_result
            orientation_source += "+consensus({})".format(consensus_votes)
            selected_metrics = rotation_scores[best_rotation]
            selected_metrics["confidence"] = round(best_confidence, 1)
            selected_metrics["format"] = bool(
                best_text
                and expected_pattern is not None
                and expected_pattern.fullmatch(best_text)
            )
            selected_metrics["text"] = best_text
            selected_metrics["total"] = round(
                min(
                    100.0,
                    max(
                        0.0,
                        0.65 * best_confidence
                        + 0.20 * selected_metrics["layout"]
                        + 15.0 * selected_metrics["baseline"]
                        + (20.0 if selected_metrics["format"] else 0.0)
                        - selected_metrics["vertical_penalty"],
                    ),
                ),
                1,
            )

    if not best_text:
        return (
            "",
            "",
            best_confidence,
            "unreadable",
            best_rotation,
            orientation_source,
            best_word_boxes,
            rotation_scores,
        )
    if expected_pattern is not None and not expected_pattern.fullmatch(best_text):
        return (
            best_raw_text,
            best_text,
            best_confidence,
            "format_mismatch",
            best_rotation,
            orientation_source,
            best_word_boxes,
            rotation_scores,
        )
    if best_confidence < config.ocr_min_confidence:
        return (
            best_raw_text,
            best_text,
            best_confidence,
            "low_confidence",
            best_rotation,
            orientation_source,
            best_word_boxes,
            rotation_scores,
        )
    return (
        best_raw_text,
        best_text,
        best_confidence,
        "recognized",
        best_rotation,
        orientation_source,
        best_word_boxes,
        rotation_scores,
    )


def assign_text_group(text, status, text_groups):
    """Assign equal normalized text to the same frame-local group."""
    if status != "recognized" or not text:
        return None
    if text not in text_groups:
        text_groups[text] = len(text_groups) + 1
    return text_groups[text]


@lru_cache(maxsize=1)
def _unicode_font_path():
    candidates = (
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    )
    return next((str(path) for path in candidates if path.exists()), None)


def draw_unicode_text(
    image,
    text,
    position,
    font_size=18,
    color=(0, 0, 255),
    max_width=None,
):
    if not text:
        return image
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    font_path = _unicode_font_path()
    draw = ImageDraw.Draw(pil_image)
    if font_path:
        font = ImageFont.truetype(font_path, font_size)
        if max_width is not None:
            while font_size > 9:
                bounds = draw.textbbox((0, 0), text, font=font)
                if bounds[2] - bounds[0] <= max_width:
                    break
                font_size -= 1
                font = ImageFont.truetype(font_path, font_size)
    else:
        font = ImageFont.load_default()
    draw.text(position, text, font=font, fill=color[::-1])
    return cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)


def annotate_objects(
    image,
    found_contours,
    config=None,
    category_names=None,
    category_overrides=None,
    text_group_registry=None,
):
    config = config or load_detector_config()
    category_names = category_names or {
        1: "Острые и скругленные",
        2: "Другое",
    }
    category_overrides = category_overrides or {}
    result = image.copy()
    valid_contours = [
        contour
        for contour in found_contours
        if cv2.contourArea(contour) >= config.min_area
    ]
    valid_contours = remove_nested_contours(valid_contours)
    if valid_contours:
        valid_contours, _ = contours.sort_contours(valid_contours)

    detections = []
    object_classes = []
    text_groups = text_group_registry if text_group_registry is not None else {}
    for object_index, contour in enumerate(valid_contours, start=1):
        class_id, similarity_score, corner_style, vertex_count = assign_class_id(
            contour, object_classes, config
        )
        rectangle = cv2.minAreaRect(contour)
        box = perspective.order_points(cv2.boxPoints(rectangle))
        if corner_style in ("sharp", "rounded"):
            category = 1
            content, mark_score = detect_internal_marking(image, box, config)
            if config.ocr_enabled:
                (
                    ocr_raw_text,
                    ocr_text,
                    ocr_confidence,
                    ocr_status,
                    ocr_rotation,
                    ocr_orientation_source,
                    ocr_word_boxes,
                    ocr_rotation_scores,
                ) = recognize_internal_text(image, box, config)
                if ocr_status == "recognized":
                    content = "signed"
                elif content == "blank" and ocr_status == "unreadable":
                    ocr_status = "blank"
            else:
                (
                    ocr_raw_text,
                    ocr_text,
                    ocr_confidence,
                    ocr_status,
                    ocr_rotation,
                    ocr_orientation_source,
                    ocr_word_boxes,
                    ocr_rotation_scores,
                ) = (
                    "",
                    "",
                    0.0,
                    "blank",
                    0,
                    "blank",
                    [],
                    {},
                )
        else:
            category = 2
            content, mark_score = "n/a", 0.0
            (
                ocr_raw_text,
                ocr_text,
                ocr_confidence,
                ocr_status,
                ocr_rotation,
                ocr_orientation_source,
                ocr_word_boxes,
                ocr_rotation_scores,
            ) = (
                "",
                "",
                0.0,
                "not_applicable",
                0,
                "not_applicable",
                [],
                {},
            )
        text_group = assign_text_group(ocr_text, ocr_status, text_groups)
        if config.ocr_console_debug:
            score_summary = ",".join(
                "{}:{:.1f}".format(rotation, metrics["total"])
                for rotation, metrics in ocr_rotation_scores.items()
            ) or "-"
            print(
                "[OCR] object={} raw={!r} normalized={!r} confidence={:.1f}% "
                "status={} rotation={} orientation={} scores={} text_group={}".format(
                    object_index,
                    ocr_raw_text,
                    ocr_text,
                    ocr_confidence,
                    ocr_status,
                    ocr_rotation,
                    ocr_orientation_source,
                    score_summary,
                    text_group if text_group is not None else "-",
                ),
                flush=True,
            )
        category = int(category_overrides.get(object_index, category))
        category_name = category_names.get(
            category, "Категория {}".format(category)
        )

        top_left, top_right, bottom_right, bottom_left = box
        top_mid = midpoint(top_left, top_right)
        bottom_mid = midpoint(bottom_left, bottom_right)
        left_mid = midpoint(top_left, bottom_left)
        right_mid = midpoint(top_right, bottom_right)
        center_x = int(round(rectangle[0][0]))
        center_y = int(round(rectangle[0][1]))
        detections.append(
            {
                "object": object_index,
                "category": category,
                "category_name": category_name,
                "id": class_id,
                "center": (center_x, center_y),
                "box": box.astype("int").tolist(),
                "box_float": box.tolist(),
                "similarity": similarity_score,
                "corner_style": corner_style,
                "corner_vertices": vertex_count,
                "content": content,
                "mark_score": mark_score,
                "ocr_text": ocr_text,
                "ocr_raw_text": ocr_raw_text,
                "ocr_confidence": ocr_confidence,
                "ocr_status": ocr_status,
                "ocr_rotation": ocr_rotation,
                "ocr_orientation_source": ocr_orientation_source,
                "ocr_word_boxes": ocr_word_boxes,
                "ocr_rotation_scores": ocr_rotation_scores,
                "text_group": text_group,
            }
        )

        cv2.drawContours(result, [box.astype("int")], -1, (0, 255, 0), 2)
        cv2.line(
            result,
            tuple(map(int, top_mid)),
            tuple(map(int, bottom_mid)),
            (255, 0, 255),
            2,
        )
        cv2.line(
            result,
            tuple(map(int, left_mid)),
            tuple(map(int, right_mid)),
            (255, 0, 255),
            2,
        )
        cv2.circle(result, (center_x, center_y), 6, (0, 255, 255), -1)
        if text_group is not None:
            text_label = "T-{}".format(text_group)
        elif ocr_status == "blank":
            text_label = "blank"
        elif ocr_status in ("not_applicable", "disabled"):
            text_label = content
        else:
            text_label = "T-?"
        label = "C{} ID-{} {} ({}, {})".format(
            category, class_id, text_label, center_x, center_y
        )
        (label_width, label_height), _ = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2
        )
        label_x = min(max(center_x + 10, 5), result.shape[1] - label_width - 5)
        label_y = min(max(center_y - 10, label_height + 5), result.shape[0] - 5)
        cv2.putText(
            result,
            label,
            (label_x, label_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 255),
            2,
        )
        if ocr_text:
            text_y = min(label_y + label_height + 8, result.shape[0] - 24)
            result = draw_unicode_text(
                result,
                "OCR: {}".format(ocr_text),
                (label_x, text_y),
                font_size=18,
                color=(0, 0, 255),
            )

    return result, detections


def process_frame(
    image,
    min_area=None,
    similarity_threshold=None,
    signed_threshold=None,
    config=None,
    category_names=None,
    category_overrides=None,
    text_group_registry=None,
):
    config = replace(config or load_detector_config())
    if min_area is not None:
        config.min_area = min_area
    if similarity_threshold is not None:
        config.similarity_threshold = similarity_threshold
    if signed_threshold is not None:
        config.signed_threshold = signed_threshold
    validate_config(config)

    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise ValueError("Input frame is empty")
    if len(image.shape) != 3 or image.shape[2] != 3:
        raise ValueError("Input frame must be a BGR color image")
    if (
        image.shape[1] != config.target_width
        or image.shape[0] != config.target_height
    ):
        image = cv2.resize(image, (config.target_width, config.target_height))
    else:
        image = image.copy()

    raw_edges, closed_edges = create_edge_maps(image, config)
    found_contours = find_objects(closed_edges)
    result, detections = annotate_objects(
        image,
        found_contours,
        config,
        category_names,
        category_overrides,
        text_group_registry,
    )
    return image, raw_edges, closed_edges, result, detections


def process_image(
    image_path,
    min_area=None,
    similarity_threshold=None,
    signed_threshold=None,
    config=None,
    category_names=None,
    category_overrides=None,
    text_group_registry=None,
):
    config = replace(config or load_detector_config())
    if min_area is not None:
        config.min_area = min_area
    if similarity_threshold is not None:
        config.similarity_threshold = similarity_threshold
    if signed_threshold is not None:
        config.signed_threshold = signed_threshold
    validate_config(config)

    image = load_image(image_path, config)
    return process_frame(
        image,
        config=config,
        category_names=category_names,
        category_overrides=category_overrides,
        text_group_registry=text_group_registry,
    )


def _debug_tile(title, image, size=(320, 240)):
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    tile = cv2.resize(image, size)
    cv2.rectangle(tile, (0, 0), (size[0], 28), (0, 0, 0), -1)
    cv2.putText(
        tile,
        title,
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return tile


def _ocr_debug_tile(title, image, subtitle="", details="", size=(480, 300)):
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    canvas = np.zeros((size[1], size[0], 3), dtype="uint8")
    header_height = 74 if details else (54 if subtitle else 28)
    available_height = size[1] - header_height
    height, width = image.shape[:2]
    scale = min(size[0] / width, available_height / height)
    display_width = max(1, int(round(width * scale)))
    display_height = max(1, int(round(height * scale)))
    resized = cv2.resize(
        image,
        (display_width, display_height),
        interpolation=cv2.INTER_NEAREST,
    )
    x = (size[0] - display_width) // 2
    y = header_height + (available_height - display_height) // 2
    canvas[y : y + display_height, x : x + display_width] = resized
    title_scale = 0.48
    title_width = cv2.getTextSize(
        title, cv2.FONT_HERSHEY_SIMPLEX, title_scale, 1
    )[0][0]
    if title_width > size[0] - 16:
        title_scale *= (size[0] - 16) / float(title_width)
    cv2.putText(
        canvas,
        title,
        (8, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        title_scale,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    if subtitle:
        canvas = draw_unicode_text(
            canvas,
            subtitle,
            (8, 25),
            font_size=18,
            color=(0, 0, 255),
            max_width=size[0] - 16,
        )
    if details:
        detail_scale = 0.42
        detail_width = cv2.getTextSize(
            details, cv2.FONT_HERSHEY_SIMPLEX, detail_scale, 1
        )[0][0]
        if detail_width > size[0] - 16:
            detail_scale *= (size[0] - 16) / float(detail_width)
        cv2.putText(
            canvas,
            details,
            (8, 68),
            cv2.FONT_HERSHEY_SIMPLEX,
            detail_scale,
            (180, 180, 180),
            1,
            cv2.LINE_AA,
        )
    return canvas


def _rotate_ocr_patch(image, rotation):
    if rotation == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if rotation == -90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rotation == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    return image


def _highlight_ocr_words(image, word_boxes, status):
    if len(image.shape) == 2:
        highlighted = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    else:
        highlighted = image.copy()
    color = (0, 180, 0) if status == "recognized" else (0, 140, 255)
    for word in word_boxes:
        x, y, width, height = word["box"]
        cv2.rectangle(
            highlighted,
            (x, y),
            (x + width, y + height),
            color,
            2,
            cv2.LINE_AA,
        )
    return highlighted


def _ocr_rotation_comparison(before, after):
    before_bgr = (
        cv2.cvtColor(before, cv2.COLOR_GRAY2BGR)
        if len(before.shape) == 2
        else before.copy()
    )
    after_bgr = (
        cv2.cvtColor(after, cv2.COLOR_GRAY2BGR)
        if len(after.shape) == 2
        else after.copy()
    )
    header_height = 28
    content_height = max(before_bgr.shape[0], after_bgr.shape[0])
    gap = 12
    width = before_bgr.shape[1] + gap + after_bgr.shape[1]
    canvas = np.full(
        (content_height + header_height, width, 3), 255, dtype="uint8"
    )

    before_y = header_height + (content_height - before_bgr.shape[0]) // 2
    after_x = before_bgr.shape[1] + gap
    after_y = header_height + (content_height - after_bgr.shape[0]) // 2
    canvas[
        before_y : before_y + before_bgr.shape[0],
        0 : before_bgr.shape[1],
    ] = before_bgr
    canvas[
        after_y : after_y + after_bgr.shape[0],
        after_x : after_x + after_bgr.shape[1],
    ] = after_bgr
    cv2.putText(
        canvas,
        "BEFORE",
        (6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.putText(
        canvas,
        "AFTER",
        (after_x + 6, 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        (0, 0, 0),
        1,
        cv2.LINE_AA,
    )
    cv2.arrowedLine(
        canvas,
        (before_bgr.shape[1] + 1, header_height // 2),
        (after_x - 1, header_height // 2),
        (0, 0, 0),
        1,
        tipLength=0.35,
    )
    return canvas


def create_ocr_debug_view(image, detections, config=None):
    config = config or load_detector_config()
    tiles = []
    for detection in detections:
        if detection["corner_style"] not in ("sharp", "rounded"):
            continue
        box = np.asarray(
            detection.get("box_float", detection["box"]), dtype="float32"
        )
        before_rotation = _prepare_ocr_patch(image, box, config)
        after_rotation = _rotate_ocr_patch(
            before_rotation, detection.get("ocr_rotation", 0)
        )
        after_rotation = _highlight_ocr_words(
            after_rotation,
            detection.get("ocr_word_boxes", []),
            detection.get("ocr_status", "unreadable"),
        )
        comparison = _ocr_rotation_comparison(
            before_rotation, after_rotation
        )
        title = "OCR #{} -> rot={} [{}] {} {:.1f}%".format(
            detection["object"],
            detection.get("ocr_rotation", 0),
            detection.get("ocr_orientation_source", "unknown"),
            detection["ocr_status"],
            detection["ocr_confidence"],
        )
        if detection["ocr_text"]:
            recognized = detection["ocr_text"]
        elif detection["ocr_status"] == "blank":
            recognized = "<blank>"
        elif detection["ocr_status"] == "disabled":
            recognized = "<OCR disabled>"
        elif detection["ocr_status"] == "noise_rejected":
            recognized = "<noise rejected>"
        else:
            recognized = "<text not recognized>"
        rotation_scores = detection.get("ocr_rotation_scores", {})
        score_details = "Scores: " + " ".join(
            "{}={:.1f}".format(rotation, rotation_scores[rotation]["total"])
            for rotation in (0, 90, -90, 180)
            if rotation in rotation_scores
        )
        tiles.append(
            _ocr_debug_tile(
                title,
                comparison,
                "OCR: {}".format(recognized),
                score_details if rotation_scores else "",
            )
        )

    if not tiles:
        tiles.append(
            _ocr_debug_tile(
                "OCR: no rectangular objects",
                np.full((120, 240), 255, dtype="uint8"),
            )
        )
    columns = 2
    while len(tiles) % columns:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [
        np.hstack(tiles[index : index + columns])
        for index in range(0, len(tiles), columns)
    ]
    return np.vstack(rows)


def create_debug_view(
    image,
    raw_edges,
    closed_edges,
    result,
    object_count,
    config=None,
    detections=None,
):
    config = config or load_detector_config()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(
        gray, (config.blur_kernel, config.blur_kernel), 0
    )
    tiles = [
        _debug_tile(
            "1. Original {}x{}".format(
                config.target_width, config.target_height
            ),
            image,
        ),
        _debug_tile("2. Grayscale", gray),
        _debug_tile("3. Gaussian blur {}x{}".format(config.blur_kernel, config.blur_kernel), blurred),
        _debug_tile(
            "4. Canny {}/{}".format(config.canny_low, config.canny_high),
            raw_edges,
        ),
        _debug_tile(
            "5. Morphology x{}".format(config.morph_iterations), closed_edges
        ),
        _debug_tile(
            "6. Objects: {} | area >= {}".format(
                object_count, int(config.min_area)
            ),
            result,
        ),
    ]
    panel = np.vstack((np.hstack(tiles[:3]), np.hstack(tiles[3:])))
    if detections is not None:
        panel = np.vstack((panel, create_ocr_debug_view(image, detections, config)))
    return panel
