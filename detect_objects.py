import argparse
import json
import os
from pathlib import Path
import sys

import cv2

from object_detector import (
    create_debug_view,
    load_detector_config,
    process_frame,
    process_image,
)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Detect objects in an image or a USB camera frame."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-i", "--image", help="path to the input image")
    source.add_argument(
        "--camera",
        type=int,
        metavar="INDEX",
        help="capture one frame from a USB/V4L2 camera",
    )
    parser.add_argument(
        "--camera-warmup-frames",
        type=int,
        default=10,
        metavar="COUNT",
        help="frames to read before processing a camera image (default: 10)",
    )
    parser.add_argument(
        "--min-area",
        type=float,
        default=None,
        help="minimum contour area; default comes from detector_config.json",
    )
    parser.add_argument("-o", "--output", help="path for the annotated image")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show the visual debug panel when a display is available",
    )
    parser.add_argument(
        "--debug-output",
        help="path for saving the visual debug panel",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="never open an OpenCV window (for Raspberry Pi OS Lite/SSH)",
    )
    parser.add_argument(
        "--json-output",
        metavar="PATH",
        help="write machine-readable results to PATH, or '-' for stdout",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=None,
        help="maximum shape difference; default comes from detector_config.json",
    )
    parser.add_argument(
        "--signed-threshold",
        type=float,
        default=None,
        help="minimum internal-mark ratio; default comes from detector_config.json",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="disable OCR text recognition and text grouping",
    )
    parser.add_argument(
        "--ocr-language",
        default=None,
        help="Tesseract language; default comes from detector_config.json",
    )
    parser.add_argument(
        "--ocr-min-confidence",
        type=float,
        default=None,
        help="minimum OCR confidence; default comes from detector_config.json",
    )
    return parser.parse_args()


def _display_available():
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _capture_camera_frame(camera_index, config, warmup_frames):
    backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
    capture = cv2.VideoCapture(camera_index, backend)
    if not capture.isOpened() and backend != cv2.CAP_ANY:
        capture.release()
        capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        capture.release()
        raise ValueError("Unable to open camera index {}".format(camera_index))

    try:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.target_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.target_height)
        frame = None
        for _ in range(max(1, warmup_frames)):
            success, candidate = capture.read()
            if success and candidate is not None:
                frame = candidate
        if frame is None:
            raise ValueError(
                "Unable to read a frame from camera index {}".format(camera_index)
            )
        return frame
    finally:
        capture.release()


def _detection_payload(detection):
    center_x, center_y = detection["center"]
    return {
        "object": detection["object"],
        "category": {
            "id": detection["category"],
            "name": detection["category_name"],
        },
        "shape_id": detection["id"],
        "center_px": {"x": center_x, "y": center_y},
        "box_px": detection["box"],
        "similarity": detection["similarity"],
        "corner_style": detection["corner_style"],
        "corner_vertices": detection["corner_vertices"],
        "content": detection["content"],
        "mark_score": detection["mark_score"],
        "text": {
            "group": detection["text_group"],
            "value": detection["ocr_text"],
            "raw": detection["ocr_raw_text"],
            "confidence": detection["ocr_confidence"],
            "status": detection["ocr_status"],
            "rotation": detection["ocr_rotation"],
            "orientation_source": detection["ocr_orientation_source"],
        },
    }


def _result_payload(source, image, detections):
    return {
        "schema_version": 1,
        "source": source,
        "image": {"width": image.shape[1], "height": image.shape[0]},
        "object_count": len(detections),
        "objects": [_detection_payload(item) for item in detections],
    }


def _write_json(payload, destination):
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if destination == "-":
        sys.stdout.write(serialized)
        return
    Path(destination).write_text(serialized, encoding="utf-8")


def _print_detections(detections, stream):
    if not detections:
        print("No objects found.", file=stream)
        return
    for detection in detections:
        center_x, center_y = detection["center"]
        print(
            "Object {}: category={} ({}), id={}, center=({}, {}) px, corners={} ({}), "
            "content={}, mark_score={:.4f}, text_group={}, text={!r}, "
            "ocr_confidence={:.1f}, ocr_status={}".format(
                detection["object"],
                detection["category"],
                detection["category_name"],
                detection["id"],
                center_x,
                center_y,
                detection["corner_style"],
                detection["corner_vertices"],
                detection["content"],
                detection["mark_score"],
                detection["text_group"],
                detection["ocr_text"],
                detection["ocr_confidence"],
                detection["ocr_status"],
            ),
            file=stream,
        )


def main():
    args = parse_arguments()
    if args.camera is not None and args.camera < 0:
        raise SystemExit("--camera must be zero or greater")
    if args.camera_warmup_frames < 1:
        raise SystemExit("--camera-warmup-frames must be at least one")
    if args.min_area is not None and args.min_area < 0:
        raise SystemExit("--min-area must be zero or greater")
    if args.similarity_threshold is not None and args.similarity_threshold < 0:
        raise SystemExit("--similarity-threshold must be zero or greater")
    if args.signed_threshold is not None and not 0 <= args.signed_threshold <= 1:
        raise SystemExit("--signed-threshold must be between zero and one")
    if args.ocr_min_confidence is not None and not 0 <= args.ocr_min_confidence <= 100:
        raise SystemExit("--ocr-min-confidence must be between zero and 100")

    human_stream = sys.stderr if args.json_output == "-" else sys.stdout
    try:
        config = load_detector_config()
        if args.min_area is not None:
            config.min_area = args.min_area
        if args.similarity_threshold is not None:
            config.similarity_threshold = args.similarity_threshold
        if args.signed_threshold is not None:
            config.signed_threshold = args.signed_threshold
        if args.no_ocr:
            config.ocr_enabled = False
        if args.ocr_language is not None:
            config.ocr_language = args.ocr_language
        if args.ocr_min_confidence is not None:
            config.ocr_min_confidence = args.ocr_min_confidence
        if args.json_output == "-":
            config.ocr_console_debug = False

        if args.image:
            source = {"type": "image", "path": str(Path(args.image).resolve())}
            image, raw_edges, closed_edges, result, detections = process_image(
                args.image, config=config
            )
        else:
            source = {
                "type": "camera",
                "index": args.camera,
                "device": "/dev/video{}".format(args.camera)
                if sys.platform.startswith("linux")
                else None,
            }
            frame = _capture_camera_frame(
                args.camera, config, args.camera_warmup_frames
            )
            image, raw_edges, closed_edges, result, detections = process_frame(
                frame, config=config
            )
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from error

    _print_detections(detections, human_stream)

    if args.output:
        if not cv2.imwrite(args.output, result):
            raise SystemExit("Unable to save result: {}".format(args.output))
        print("Result saved to: {}".format(args.output), file=human_stream)

    debug_view = None
    if args.debug or args.debug_output:
        debug_view = create_debug_view(
            image,
            raw_edges,
            closed_edges,
            result,
            len(detections),
            config,
            detections,
        )

    if args.debug_output:
        if not cv2.imwrite(args.debug_output, debug_view):
            raise SystemExit(
                "Unable to save debug view: {}".format(args.debug_output)
            )
        print(
            "Debug view saved to: {}".format(args.debug_output),
            file=human_stream,
        )

    if args.json_output:
        try:
            _write_json(
                _result_payload(source, image, detections), args.json_output
            )
        except OSError as error:
            raise SystemExit("Unable to save JSON result: {}".format(error)) from error
        if args.json_output != "-":
            print(
                "JSON result saved to: {}".format(args.json_output),
                file=human_stream,
            )

    should_display = not args.no_display and _display_available()
    if should_display:
        window_title = "Visual debug" if args.debug else "Detected objects"
        cv2.imshow(window_title, debug_view if args.debug else result)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    elif not args.no_display:
        print(
            "No graphical display detected; result was processed without a window.",
            file=human_stream,
        )


if __name__ == "__main__":
    main()
