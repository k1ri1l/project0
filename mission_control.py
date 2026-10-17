import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PointStamped
from nav_msgs.msg import Odometry
import tkinter as tk
import subprocess
import math

BASE = (-3.2, -3.2)
UNLOAD_1 = (3.2, -3.2)
UNLOAD_2 = (2.5, 3.2)

class MissionControl(Node):
    def __init__(self):
        super().__init__('mission_control')
        self._action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.sub_point = self.create_subscription(PointStamped, '/clicked_point', self.on_click_point, 10)
        self.sub_odom = self.create_subscription(Odometry, '/odom', self.on_odom, 10)
        self.state = 'IDLE'
        self.current_target = 1
        self.obs_count = 0
        self.current_goal_handle = None
        self.robot_x = -3.2
        self.robot_y = -3.2
        self.robot_yaw = 0.0

    def on_odom(self, msg):
        self.robot_x = msg.pose.pose.position.x
        self.robot_y = msg.pose.pose.position.y
        q = msg.pose.pose.orientation
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        self.robot_yaw = math.atan2(siny_cosp, cosy_cosp)

    def send_goal(self, x, y):
        if not self._action_client.wait_for_server(timeout_sec=1.0):
            return
        msg = NavigateToPose.Goal()
        msg.pose.header.frame_id = 'map'
        msg.pose.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(x)
        msg.pose.pose.position.y = float(y)
        msg.pose.pose.orientation.w = 1.0
        future = self._action_client.send_goal_async(msg)
        future.add_done_callback(self.goal_response_callback)

    def goal_response_callback(self, future):
        self.current_goal_handle = future.result()
        if not self.current_goal_handle.accepted:
            return
        res = self.current_goal_handle.get_result_async()
        res.add_done_callback(self.get_result_callback)

    def get_result_callback(self, future):
        if self.state == 'PATROL':
            if self.current_target == 1:
                self.current_target = 2
                self.send_goal(*UNLOAD_2)
            else:
                self.current_target = 1
                self.send_goal(*UNLOAD_1)
        elif self.state == 'HOME':
            self.state = 'IDLE'

    def stop_robot(self):
        self.state = 'IDLE'
        if self.current_goal_handle:
            self.current_goal_handle.cancel_goal_async()

    def spawn_box(self, x, y):
        self.obs_count += 1
        name = f"obs_dyn_{self.obs_count}"
        sdf = f'<sdf version="1.6"><model name="{name}"><static>true</static><pose>{x} {y} 0.4 0 0 0</pose><link name="l"><collision name="c"><geometry><box><size>0.6 0.6 0.8</size></geometry></collision><visual name="v"><geometry><box><size>0.6 0.6 0.8</size></geometry><material><ambient>1 0 0 1</ambient><diffuse>1 0 0 1</diffuse></material></visual></link></model></sdf>'
        subprocess.Popen(['ros2', 'run', 'ros_ign_gazebo', 'create', '-string', sdf, '-name', name])

    def on_click_point(self, msg):
        self.spawn_box(msg.point.x, msg.point.y)

    def spawn_ahead(self):
        dist = 1.2
        ox = self.robot_x + dist * math.cos(self.robot_yaw)
        oy = self.robot_y + dist * math.sin(self.robot_yaw)
        self.spawn_box(ox, oy)

def main():
    rclpy.init()
    node = MissionControl()
    root = tk.Tk()
    root.title("Управление челноком")
    root.geometry("360x320")
    root.resizable(False, False)

    lbl = tk.Label(root, text="Статус: Готов к запуску", font=("Arial", 11, "bold"))
    lbl.pack(pady=10)

    def on_start():
        node.state = 'PATROL'
        node.current_target = 1
        lbl.config(text="Курсирует: Разгрузка 1 ⇄ 2", fg="green")
        node.send_goal(*UNLOAD_1)

    def on_home():
        node.state = 'HOME'
        lbl.config(text="Возврат на БАЗУ...", fg="blue")
        node.send_goal(*BASE)

    def on_stop():
        node.stop_robot()
        lbl.config(text="Статус: Остановлен", fg="red")

    tk.Button(root, text="▶ ЗАПУСК (Разгрузка 1 ⇄ 2)", font=("Arial", 10, "bold"), bg="#4CAF50", fg="white", height=2, command=on_start).pack(fill="x", padx=20, pady=4)
    tk.Button(root, text="🏠 ДОМОЙ (На Базу)", font=("Arial", 10, "bold"), bg="#2196F3", fg="white", height=2, command=on_home).pack(fill="x", padx=20, pady=4)
    tk.Button(root, text="⏹ СТОП", font=("Arial", 9), bg="#f44336", fg="white", command=on_stop).pack(fill="x", padx=20, pady=4)
    tk.Button(root, text="🧱 ПОСТАВИТЬ СТЕНУ ПЕРЕД РОБОТОМ", font=("Arial", 9, "bold"), bg="#FF9800", fg="white", command=node.spawn_ahead).pack(fill="x", padx=20, pady=6)
    tk.Label(root, text="Или используй 'Publish Point' в RViz для клика на карту", fg="#666", font=("Arial", 8)).pack(pady=2)

    def ros_loop():
        rclpy.spin_once(node, timeout_sec=0)
        root.after(20, ros_loop)

    root.after(20, ros_loop)
    root.mainloop()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
