import os
from launch import LaunchDescription
from launch.actions import SetEnvironmentVariable, ExecuteProcess
from launch_ros.actions import Node

def generate_launch_description():
    # Жесткий и надежный путь к вашему файлу в исходниках src
    world_file_path = '/home/valerii/proect_manipulator/src/gazebo/worlds/my_new_world.sdf'
    
    # Путь к папке worlds (чтобы Gazebo знала, где искать папки моделей box и ground)
    worlds_dir = '/home/valerii/proect_manipulator/src/gazebo/worlds'

    # 1. Запуск Gazebo напрямую как процесса ОС (минуя посредников)
    gazebo_process = ExecuteProcess(
        cmd=['gz', 'sim', '-r', world_file_path],
        output='screen'
    )

    # 2. Нода моста ROS <-> Gazebo для двух раздельных топиков
    ros_gz_bridge_node = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=[
            '/cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist',
            '/cmd_vel_y@geometry_msgs/msg/Twist@gz.msgs.Twist',
            '/cmd_vel_z@geometry_msgs/msg/Twist@gz.msgs.Twist',
            '/cmd_vel_rotate@std_msgs/msg/Float64@gz.msgs.Double',
            '/world/manipulator_V2/model/box4/link/zahvat/sensor/left_finger_contact/contact@ros_gz_interfaces/msg/Contacts[gz.msgs.Contacts',
            '/manipulator/vacuum_suction@std_msgs/msg/Bool]gz.msgs.Boolean',
            '/gripper/attach@std_msgs/msg/Empty@gz.msgs.Empty',
            '/gripper/detach@std_msgs/msg/Empty@gz.msgs.Empty',
            '/gripper/status@std_msgs/msg/Bool[gz.msgs.Boolean',
            '/conveyor/cmd_vel@std_msgs/msg/Float64]gz.msgs.Double',
            '/camera/image_raw@sensor_msgs/msg/Image[gz.msgs.Image'


        ],
        output='screen'
    )

    return LaunchDescription([
        # Принудительно заставляем Gazebo искать модели в вашей папке src
        SetEnvironmentVariable('GZ_SIM_RESOURCE_PATH', worlds_dir),
        
        # Системные плагины для Jazzy
        SetEnvironmentVariable('GZ_SIM_SYSTEM_PLUGIN_PATH', 
                               '/opt/ros/jazzy/opt/gz_sim_vendor/lib/gz-sim-8/plugins:/usr/lib/x86_64-linux-gnu/gz-sim-8/plugins'),

        # Запускаем симулятор напрямую
        gazebo_process,

        # Запускаем мост
        ros_gz_bridge_node
    ])
