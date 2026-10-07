#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export GZ_PARTITION="${GZ_PARTITION:-kirillka_aruco_marker}"
resource_path="$project_dir/models"
gui_plugin_path="$project_dir/build/live_transform_control"
if [[ -n "${GZ_SIM_RESOURCE_PATH:-}" ]]; then
  resource_path="$resource_path:$GZ_SIM_RESOURCE_PATH"
fi
if command -v gz >/dev/null 2>&1; then
  export GZ_GUI_PLUGIN_PATH="$gui_plugin_path${GZ_GUI_PLUGIN_PATH:+:$GZ_GUI_PLUGIN_PATH}"
  gz_command=(gz)
  sim_version=()
  detector_command=()
elif command -v distrobox-enter >/dev/null 2>&1; then
  gz_command=(distrobox-enter -n ubuntu-1 -- env GZ_PARTITION="$GZ_PARTITION" GZ_SIM_RESOURCE_PATH="$resource_path" GZ_GUI_PLUGIN_PATH="$gui_plugin_path" QT_QPA_PLATFORM=xcb gz)
  detector_command=(distrobox-enter -n ubuntu-1 -- env GZ_PARTITION="$GZ_PARTITION" GZ_SIM_RESOURCE_PATH="$resource_path" "$project_dir/.venv/bin/python" "$project_dir/detect_aruco.py")
  sim_version=(--force-version 8)
else
  echo 'Для запуска нужен Gazebo Sim (gz sim) или контейнер ubuntu-1 с Gazebo Sim 8.' >&2
  exit 1
fi

if [[ ${#detector_command[@]} -gt 0 ]]; then
  if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    echo 'Не найдена .venv. Создайте её и установите зависимости из requirements.txt.' >&2
    exit 1
  fi
  distrobox-enter -n ubuntu-1 -- cmake \
    -S "$project_dir/live_transform_control" \
    -B "$gui_plugin_path" \
    -DCMAKE_BUILD_TYPE=Release
  distrobox-enter -n ubuntu-1 -- cmake --build "$gui_plugin_path" -j2
  "${detector_command[@]}" >> "$project_dir/aruco_detector.log" 2>&1 &
  detector_pid=$!
  cleanup_detector() {
    kill "$detector_pid" 2>/dev/null || true
    wait "$detector_pid" 2>/dev/null || true
  }
  trap cleanup_detector EXIT INT TERM
fi

set +e
"${gz_command[@]}" sim "${sim_version[@]}" -r "$project_dir/camera_dancer.sdf" "$@"
sim_status=$?
exit "$sim_status"
