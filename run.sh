#!/bin/bash
set -e

source /opt/ros/humble/setup.bash

echo "=== 1. Запуск Ignition Gazebo с миром warehouse.sdf ==="
ign gazebo -r ~/ign_bot_ws/warehouse.sdf &
IGN_PID=$!
sleep 4

echo "=== 2. Запуск ROS-Ignition Bridge ==="
ros2 run ros_gz_bridge parameter_bridge \
  /cmd_vel@geometry_msgs/msg/Twist]ignition.msgs.Twist \
  /odom@nav_msgs/msg/Odometry[ignition.msgs.Odometry \
  /scan@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan \
  /imu@sensor_msgs/msg/Imu[ignition.msgs.IMU \
  /cliff/front_left@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan \
  /cliff/front_right@sensor_msgs/msg/LaserScan[ignition.msgs.LaserScan &
BRIDGE_PID=$!
sleep 2

echo "=== 3. Запуск пульта shuttle_ignition.py ==="
python3 ~/ign_bot_ws/shuttle_ignition.py

kill $BRIDGE_PID $IGN_PID 2>/dev/null || true
