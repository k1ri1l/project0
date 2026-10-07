#!/usr/bin/env python3
"""Detect ArUco ID 23 on a Gazebo image topic and publish an annotated view."""

from __future__ import annotations

import signal
import sys
import threading
import time

import cv2
import numpy as np
from gz.msgs10.image_pb2 import Image
from gz.transport13 import Node


CAMERA_TOPIC = "/front_camera/image"
OUTPUT_TOPIC = "/aruco_camera/annotated"
MARKER_ID = 23
MARKER_SIZE_METERS = 0.45
HORIZONTAL_FOV = np.deg2rad(60.0)
PIXEL_FORMAT = Image.DESCRIPTOR.fields_by_name["pixel_format_type"].enum_type.values_by_name
RGB_INT8 = PIXEL_FORMAT["RGB_INT8"].number
BGR_INT8 = PIXEL_FORMAT["BGR_INT8"].number

stop_event = threading.Event()
print_lock = threading.Lock()
last_status: tuple[str, ...] | None = None
last_print_time = 0.0


def report(status: tuple[str, ...]) -> None:
    global last_status, last_print_time
    now = time.monotonic()
    with print_lock:
        if status != last_status or now - last_print_time >= 2.0:
            print("[ArUco] " + " | ".join(status), flush=True)
            last_status = status
            last_print_time = now


def main() -> int:
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    detector = aruco.ArucoDetector(dictionary, aruco.DetectorParameters())
    node = Node()
    publisher = node.advertise(OUTPUT_TOPIC, Image)
    if publisher is None or not publisher.valid():
        print(f"Cannot advertise {OUTPUT_TOPIC}", file=sys.stderr)
        return 1

    def on_image(message: Image) -> None:
        try:
            if message.width <= 0 or message.height <= 0:
                return
            if message.pixel_format_type not in (RGB_INT8, BGR_INT8):
                report((f"unsupported pixel format {message.pixel_format_type}",))
                return
            channels = 3
            image = np.frombuffer(message.data, dtype=np.uint8)
            image = image.reshape((message.height, message.width, channels))
            if message.pixel_format_type == RGB_INT8:
                frame = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            elif channels == 3:
                frame = image.copy()
            else:
                frame = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

            corners, ids, _ = detector.detectMarkers(frame)
            output = frame.copy()
            found = ids is not None and MARKER_ID in ids.flatten().tolist()
            if found:
                aruco.drawDetectedMarkers(output, corners, ids)
                index = ids.flatten().tolist().index(MARKER_ID)
                marker_corners = corners[index][0]
                center = marker_corners.mean(axis=0)
                center_x, center_y = int(center[0]), int(center[1])
                pixel_width = float(np.linalg.norm(marker_corners[0] - marker_corners[1]))
                focal_px = message.width / (2.0 * np.tan(HORIZONTAL_FOV / 2.0))
                distance = focal_px * MARKER_SIZE_METERS / max(pixel_width, 1.0)
                cv2.circle(output, (center_x, center_y), 5, (0, 255, 0), -1)
                text = f"ArUco ID {MARKER_ID}  {distance:.2f} m"
                cv2.putText(output, text, (16, 34), cv2.FONT_HERSHEY_SIMPLEX,
                            0.8, (0, 210, 0), 2, cv2.LINE_AA)
                report((f"ID={MARKER_ID}", f"center=({center_x},{center_y})",
                        f"distance~{distance:.2f}m"))
            else:
                cv2.putText(output, f"ArUco ID {MARKER_ID}: not found", (16, 34),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2, cv2.LINE_AA)
                report((f"ID={MARKER_ID} not found",))

            if message.pixel_format_type == RGB_INT8:
                output = cv2.cvtColor(output, cv2.COLOR_BGR2RGB)
            annotated = Image()
            annotated.CopyFrom(message)
            annotated.data = output.tobytes()
            publisher.publish(annotated)
        except Exception as exc:  # Keep the transport callback alive on malformed frames.
            report((f"processing error: {exc}",))

    if node.subscribe(Image, CAMERA_TOPIC, on_image) is False:
        print(f"Cannot subscribe to {CAMERA_TOPIC}", file=sys.stderr)
        return 1

    def stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    print(f"ArUco detector listening on {CAMERA_TOPIC}; annotated output: {OUTPUT_TOPIC}",
          flush=True)
    try:
        while not stop_event.wait(0.2):
            pass
    except KeyboardInterrupt:
        stop_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
