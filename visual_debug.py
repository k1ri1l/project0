import argparse

import cv2

from object_detector import create_debug_view, load_detector_config, process_image


def main():
    parser = argparse.ArgumentParser(
        description="Show object detection stages, centers, and orientation axes."
    )
    parser.add_argument("-i", "--image", required=True, help="path to the input image")
    parser.add_argument("--min-area", type=float, default=None)
    parser.add_argument("--similarity-threshold", type=float, default=None)
    parser.add_argument("--signed-threshold", type=float, default=None)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--ocr-language", default=None)
    parser.add_argument("--ocr-min-confidence", type=float, default=None)
    parser.add_argument("-o", "--output", help="optional path for the debug panel")
    args = parser.parse_args()

    if args.min_area is not None and args.min_area < 0:
        raise SystemExit("--min-area must be zero or greater")
    if args.similarity_threshold is not None and args.similarity_threshold < 0:
        raise SystemExit("--similarity-threshold must be zero or greater")
    if args.signed_threshold is not None and not 0 <= args.signed_threshold <= 1:
        raise SystemExit("--signed-threshold must be between zero and one")
    if args.ocr_min_confidence is not None and not 0 <= args.ocr_min_confidence <= 100:
        raise SystemExit("--ocr-min-confidence must be between zero and 100")

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
        image, raw_edges, closed_edges, result, detections = process_image(
            args.image, config=config
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    print("Objects processed: {}".format(len(detections)))
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
            )
        )

    debug_view = create_debug_view(
        image,
        raw_edges,
        closed_edges,
        result,
        len(detections),
        config,
        detections,
    )
    if args.output and not cv2.imwrite(args.output, debug_view):
        raise SystemExit("Unable to save debug view: {}".format(args.output))
    if args.output:
        print("Debug view saved to: {}".format(args.output))

    cv2.imshow("Visual debug", debug_view)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
