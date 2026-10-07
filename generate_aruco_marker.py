#!/usr/bin/env python3
"""Generate the printable ArUco marker and its Gazebo visual model."""

from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
MARKER_ID = 23
DICTIONARY_NAME = "DICT_4X4_50"
CELL_SIZE = 0.075
BOARD_SIZE = 0.62
PIXELS = 600


def main() -> None:
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    marker = aruco.generateImageMarker(dictionary, MARKER_ID, PIXELS, borderBits=1)

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    quiet = cv2.copyMakeBorder(marker, 75, 75, 75, 75, cv2.BORDER_CONSTANT, value=255)
    if not cv2.imwrite(str(assets / "aruco_4x4_50_id23.png"), quiet):
        raise OSError("Failed to write marker PNG")

    model_dir = ROOT / "models" / "aruco_marker"
    model_dir.mkdir(parents=True, exist_ok=True)
    cell_pixels = PIXELS // 6
    modules = marker.reshape(6, cell_pixels, 6, cell_pixels).mean(axis=(1, 3)) < 128
    rows, cols = np.where(modules)
    visuals = []
    for index, (row, col) in enumerate(zip(rows.tolist(), cols.tolist(), strict=True)):
        x = ((5 / 2) - row) * CELL_SIZE
        y = ((5 / 2) - col) * CELL_SIZE
        visuals.append(
            f"""      <visual name="cell_{index:02d}">
        <pose>{x:.6f} {y:.6f} 0.007 0 0 0</pose>
        <geometry><box><size>{CELL_SIZE:.6f} {CELL_SIZE:.6f} 0.004</size></box></geometry>
        <material>
          <ambient>0.005 0.005 0.005 1</ambient>
          <diffuse>0.005 0.005 0.005 1</diffuse>
          <specular>0 0 0 1</specular>
        </material>
      </visual>"""
        )

    model_sdf = f"""<?xml version="1.0"?>
<sdf version="1.9">
  <model name="aruco_marker">
    <static>true</static>
    <link name="board">
      <collision name="board_collision">
        <geometry><box><size>{BOARD_SIZE:.3f} {BOARD_SIZE:.3f} 0.01</size></box></geometry>
      </collision>
      <visual name="white_board">
        <geometry><box><size>{BOARD_SIZE:.3f} {BOARD_SIZE:.3f} 0.01</size></box></geometry>
        <material>
          <ambient>1 1 1 1</ambient>
          <diffuse>1 1 1 1</diffuse>
          <specular>0 0 0 1</specular>
        </material>
      </visual>
{chr(10).join(visuals)}
    </link>
  </model>
</sdf>
"""
    (model_dir / "model.sdf").write_text(model_sdf, encoding="utf-8")
    (model_dir / "model.config").write_text(
        """<?xml version="1.0"?>
<model>
  <name>ArUco marker 23</name>
  <version>1.0</version>
  <sdf version="1.9">model.sdf</sdf>
  <description>Static DICT_4X4_50 marker with ID 23.</description>
</model>
""",
        encoding="utf-8",
    )
    print(f"Generated DICT_4X4_50 marker ID {MARKER_ID}")


if __name__ == "__main__":
    main()
