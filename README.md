# ArUco-камера в Gazebo

Танцор заменён на вертикальную статичную метку ArUco `DICT_4X4_50`, ID 23,
размером 45 см. Она установлена на высоте 1,05 м, в 3,5 м перед объективом.
RGB-камера находится на высоте 1,4 м и выдаёт кадры 640 × 480 при 30 кадрах/с.

При запуске Gazebo скрипт на Python подписывается на `/front_camera/image`,
распознаёт метку, печатает её ID, центр и примерную дистанцию, а также
публикует кадр с рамкой и подписью на `/aruco_camera/annotated`. В Gazebo
это изображение открывается в панели «Камера — ArUco ID 23».
PNG метки для печати находится в `assets/aruco_4x4_50_id23.png`.

Метку можно перемещать и вращать прямо во время симуляции панелью
«Переместить / повернуть объект». Нажмите `T` (или кнопку с четырьмя стрелками),
затем отдельно щёлкните по метке, чтобы на ней появились цветные оси. Тащите
нужную ось, не отпуская кнопку мыши: новая поза отправляется в Gazebo примерно
30 раз в секунду, поэтому изображение и показания ArUco обновляются прямо во
время перетаскивания. Отпускание кнопки завершает перемещение. Нажмите `R` для
поворота. Перетаскивание плоскости или пустого места вращает вид сцены.

`run.sh` собирает небольшой GUI-плагин `LiveTransformControl` при запуске;
для сборки в контейнере `ubuntu-1` используется CMake и установленный компилятор.

OpenCV установлен только в проектную `.venv`; Gazebo Transport подключается
из системных Python-модулей контейнера `ubuntu-1`.

## Запуск

Откройте ярлык Gazebo на рабочем столе либо выполните:

```bash
bash "$HOME/Рабочий стол/GazeboSlidingCube/run.sh"
```

Первоначальная настройка `.venv` в контейнере `ubuntu-1`:

```bash
distrobox-enter -n ubuntu-1 -- python3 -m venv --system-site-packages \
  "$HOME/Рабочий стол/GazeboSlidingCube/.venv"
distrobox-enter -n ubuntu-1 -- \
  "$HOME/Рабочий стол/GazeboSlidingCube/.venv/bin/python" -m pip install \
  -r "$HOME/Рабочий стол/GazeboSlidingCube/requirements.txt"
```

Повторная генерация PNG и геометрии метки:

```bash
distrobox-enter -n ubuntu-1 -- \
  "$HOME/Рабочий стол/GazeboSlidingCube/.venv/bin/python" \
  "$HOME/Рабочий стол/GazeboSlidingCube/generate_aruco_marker.py"
```

`run.sh` запускает детектор и Gazebo вместе, затем останавливает детектор
при закрытии симуляции.

## Видеопоток

Исходный и распознанный видеопотоки доступны в Gazebo Transport:

```bash
distrobox-enter -n ubuntu-1 -- env GZ_PARTITION=kirillka_aruco_marker \
  gz topic -e -t /aruco_camera/annotated
```

Лог считывания сохраняется в `aruco_detector.log`.
