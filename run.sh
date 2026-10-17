#!/bin/bash
# Завершаем только старые узлы симуляции, не задевая сам скрипт
killall -9 ign rviz2 2>/dev/null
pkill -9 -f "mission_control.py" 2>/dev/null
pkill -9 -f "navigation_launch.py" 2>/dev/null
pkill -9 -f "online_async_launch.py" 2>/dev/null
pkill -9 -f "ros_ign_bridge" 2>/dev/null
pkill -9 -f "robot_state_publisher" 2>/dev/null
sleep 1

source /opt/ros/humble/setup.bash
source ~/ign_bot_ws/install/setup.bash

echo "=== 1. Запуск Ignition Gazebo 6 ==="
ign gazebo -r ~/ign_bot_ws/src/my_ign_bot/worlds/obstacles.sdf &
sleep 5

echo "=== 2. Мост и публикация модели ==="
ros2 run robot_state_publisher robot_state_publisher --ros-args -p robot_description:="$(cat ~/ign_bot_ws/src/my_ign_bot/urdf/robot.urdf)" -p use_sim_time:=true &
ros2 run ros_ign_bridge parameter_bridge \
  /scan@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan \
  /cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist \
  /odom@nav_msgs/msg/Odometry[ignition.msgs.Odometry \
  /tf@tf2_msgs/msg/TFMessage[ignition.msgs.Pose_V \
  /clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock &

sleep 2
ros2 run tf2_ros static_transform_publisher --frame-id laser_frame --child-frame-id "low_poly_bot/base_footprint/lidar" &
ros2 run tf2_ros static_transform_publisher --frame-id laser_frame --child-frame-id "low_poly_bot/laser_frame/lidar" &

echo "=== 3. Запуск SLAM ==="
ros2 launch slam_toolbox online_async_launch.py use_sim_time:=True &
sleep 4

echo "=== 4. Запуск Nav2 ==="
ros2 launch nav2_bringup navigation_launch.py use_sim_time:=True &
sleep 4

# Тюнинг скорости и безопасности
ros2 param set /controller_server FollowPath.max_vel_x 1.2 2>/dev/null
ros2 param set /controller_server FollowPath.max_vel_theta 2.2 2>/dev/null
ros2 param set /controller_server FollowPath.acc_lim_x 2.5 2>/dev/null
ros2 param set /local_costmap/local_costmap inflation_layer.inflation_radius 0.60 2>/dev/null
ros2 param set /global_costmap/global_costmap inflation_layer.inflation_radius 0.60 2>/dev/null

echo "=== 5. RViz2 и Панель кнопок ==="
ros2 launch nav2_bringup rviz_launch.py &
sleep 2

python3 ~/ign_bot_ws/mission_control.py

kill $(jobs -p) 2>/dev/null
