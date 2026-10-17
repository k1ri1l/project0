import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Image
from nav_msgs.msg import Odometry, Path
import tkinter as tk
import subprocess
import math
import numpy as np

# Координаты станций в мире Gazebo
STATION_HOME = (-3.0, 0.0)
STATION_A = (3.2, 2.0)
STATION_B = (3.2, -2.0)

# Границы карты в Gazebo для отрисовки на холсте (10x10 м)
WORLD_MIN_X, WORLD_MAX_X = -5.0, 5.0
WORLD_MIN_Y, WORLD_MAX_Y = -5.0, 5.0
CANVAS_SIZE = 400

def world_to_canvas(x, y):
    # Преобразование координат Gazebo в пиксели холста Tkinter
    cx = (x - WORLD_MIN_X) / (WORLD_MAX_X - WORLD_MIN_X) * CANVAS_SIZE
    cy = CANVAS_SIZE - (y - WORLD_MIN_Y) / (WORLD_MAX_Y - WORLD_MIN_Y) * CANVAS_SIZE
    return cx, cy

def canvas_to_world(cx, cy):
    # Преобразование клика мыши в холсте в координаты Gazebo
    x = WORLD_MIN_X + (cx / CANVAS_SIZE) * (WORLD_MAX_X - WORLD_MIN_X)
    y = WORLD_MIN_Y + ((CANVAS_SIZE - cy) / CANVAS_SIZE) * (WORLD_MAX_Y - WORLD_MIN_Y)
    return x, y

class ShuttleMissionControl(Node):
    def __init__(self, root):
        super().__init__('mission_control')
        self.root = root
        
        self._action_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.sub_odom = self.create_subscription(Odometry, '/odom', self.on_odom, 10)
        self.sub_plan = self.create_subscription(Path, '/plan', self.on_plan, 10)
        self.sub_depth = self.create_subscription(Image, '/camera/depth/image_raw', self.on_depth, 10)
        self.pub_cmd = self.create_publisher(Twist, '/cmd_vel', 10)

        self.robot_x = STATION_HOME[0]
        self.robot_y = STATION_HOME[1]
        self.path_points = []
        self.obstacles = []
        self.cyl_counter = 0

        self.mode = 'IDLE' # 'PATROL', 'HOME', 'IDLE'
        self.patrol_target = 'A'
        self.goal_handle = None
        self.safety_triggered = False

    def on_odom(self, msg):
        self.robot_x = msg.pose.pose.position.x
        self.robot_y = msg.pose.pose.position.y

    def on_plan(self, msg):
        self.path_points = [(p.pose.position.x, p.pose.position.y) for p in msg.poses]

    def on_depth(self, msg):
        try:
            depth = np.frombuffer(msg.data, dtype=np.float32).reshape((msg.height, msg.width))
            h, w = depth.shape
            roi = depth[int(h * 0.7):, int(w * 0.3):int(w * 0.7)]
            valid = roi[np.isfinite(roi) & (roi > 0.05)]

            # Защита от обрыва платформы или препятствия в упор
            if len(valid) < (roi.size * 0.25):
                self.trigger_safety("ОБРЫВ ПЛАТФОРМЫ!")
                return

            dist = float(np.median(valid))
            if dist > 0.42:
                self.trigger_safety(f"КРАЙ ПЛАТФОРМЫ ({dist:.2f} м)!")
            elif dist < 0.14:
                self.trigger_safety(f"ПРЕПЯТСТВИЕ В УПОР ({dist:.2f} м)!")
            else:
                self.safety_triggered = False
        except Exception:
            pass

    def trigger_safety(self, reason):
        if not self.safety_triggered and self.mode != 'IDLE':
            self.safety_triggered = True
            self.get_logger().warn(f"[СТЕРЕОКАМЕРА СТОП] {reason}")
            self.stop()

    def send_goal(self, x, y):
        if not self._action_client.wait_for_server(timeout_sec=1.0):
            return
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.orientation.w = 1.0
        fut = self._action_client.send_goal_async(goal)
        fut.add_done_callback(self.on_goal_response)

    def on_goal_response(self, fut):
        self.goal_handle = fut.result()
        if not self.goal_handle.accepted:
            return
        res_fut = self.goal_handle.get_result_async()
        res_fut.add_done_callback(self.on_goal_done)

    def on_goal_done(self, fut):
        if self.mode == 'PATROL':
            if self.patrol_target == 'A':
                self.patrol_target = 'B'
                self.send_goal(*STATION_B)
            elif self.patrol_target == 'B':
                self.patrol_target = 'HOME'
                self.send_goal(*STATION_HOME)
            else:
                self.patrol_target = 'A'
                self.send_goal(*STATION_A)
        elif self.mode == 'HOME':
            self.mode = 'IDLE'

    def start_patrol(self):
        self.mode = 'PATROL'
        self.patrol_target = 'A'
        self.send_goal(*STATION_A)

    def go_home(self):
        self.mode = 'HOME'
        self.send_goal(*STATION_HOME)

    def stop(self):
        self.mode = 'IDLE'
        if self.goal_handle:
            self.goal_handle.cancel_goal_async()
        stop_msg = Twist()
        self.pub_cmd.publish(stop_msg)

    def spawn_cylinder(self, wx, wy):
        self.cyl_counter += 1
        name = f"dyn_cyl_{self.cyl_counter}"
        self.obstacles.append((wx, wy))
        sdf = f'<sdf version="1.6"><model name="{name}"><static>true</static><pose>{wx} {wy} 0.5 0 0 0</pose><link name="l"><collision name="c"><geometry><cylinder><radius>0.25</radius><length>1.0</length></cylinder></geometry></collision><visual name="v"><geometry><cylinder><radius>0.25</radius><length>1.0</length></cylinder><material><ambient>0.85 0.25 0.2 1</ambient><diffuse>0.85 0.25 0.2 1</diffuse></material></visual></link></model></sdf>'
        subprocess.Popen(['ros2', 'run', 'ros_ign_gazebo', 'create', '-string', sdf, '-name', name])

def build_gui():
    rclpy.init()
    root = tk.Tk()
    root.title("Пульт управления: Клик мыши = Цилиндр")
    root.resizable(False, False)

    node = ShuttleMissionControl(root)

    # Верхняя панель кнопок управления
    btn_frame = tk.Frame(root)
    btn_frame.pack(fill="x", padx=10, pady=5)

    btn_start = tk.Button(
        btn_frame, 
        text="СТАРТ: Курсировать ДОМ ⇄ Кубы А и Б", 
        bg="#2ecc71", fg="white", font=("Arial", 11, "bold"), height=2,
        command=node.start_patrol
    )
    btn_start.pack(fill="x", pady=2)

    sub_btn_frame = tk.Frame(btn_frame)
    sub_btn_frame.pack(fill="x", pady=2)

    btn_home = tk.Button(
        sub_btn_frame, 
        text="🏠 ДОМОЙ", 
        bg="#3498db", fg="white", font=("Arial", 10, "bold"), width=18, height=1,
        command=node.go_home
    )
    btn_home.pack(side="left", padx=2, expand=True, fill="x")

    btn_stop = tk.Button(
        sub_btn_frame, 
        text="⏹ СТОП", 
        bg="#e74c3c", fg="white", font=("Arial", 10, "bold"), width=18, height=1,
        command=node.stop
    )
    btn_stop.pack(side="right", padx=2, expand=True, fill="x")

    lbl_info = tk.Label(root, text="Кликните в любое место карты ниже, чтобы поставить цилиндр в Gazebo:", font=("Arial", 9))
    lbl_info.pack(pady=2)

    canvas = tk.Canvas(root, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="white", highlightthickness=1, highlightbackground="#ccc")
    canvas.pack(padx=10, pady=5)

    def on_canvas_click(event):
        wx, wy = canvas_to_world(event.x, event.y)
        node.spawn_cylinder(wx, wy)

    canvas.bind("<Button-1>", on_canvas_click)

    def redraw():
        canvas.delete("all")

        # 1. Станция ДОМ
        hx, hy = world_to_canvas(*STATION_HOME)
        canvas.create_rectangle(hx-20, hy-20, hx+20, hy+20, fill="#2ecc71", outline="black")
        canvas.create_text(hx, hy, text="ДОМ", font=("Arial", 8, "bold"))

        # 2. Станция Куб А
        ax, ay = world_to_canvas(*STATION_A)
        canvas.create_rectangle(ax-24, ay-24, ax+24, ay+24, fill="#3498db", outline="black")
        canvas.create_text(ax, ay, text="Куб А", font=("Arial", 8, "bold"), fill="white")

        # 3. Станция Куб Б
        bx, by = world_to_canvas(*STATION_B)
        canvas.create_rectangle(bx-24, by-24, bx+24, by+24, fill="#e67e22", outline="black")
        canvas.create_text(bx, by, text="Куб Б", font=("Arial", 8, "bold"), fill="white")

        # 4. Цилиндры-препятствия
        for ox, oy in node.obstacles:
            cx, cy = world_to_canvas(ox, oy)
            canvas.create_oval(cx-12, cy-12, cx+12, cy+12, fill="#e74c3c", outline="black")

        # 5. Фиолетовая линия планируемой траектории
        if len(node.path_points) > 1:
            pts = []
            for px, py in node.path_points:
                cx, cy = world_to_canvas(px, py)
                pts.extend([cx, cy])
            canvas.create_line(*pts, fill="#8e44ad", width=2)

        # 6. Робот (бирюзовый маркер)
        rx, ry = world_to_canvas(node.robot_x, node.robot_y)
        canvas.create_oval(rx-8, ry-8, rx+8, ry+8, fill="#1abc9c", outline="black", width=2)

        rclpy.spin_once(node, timeout_sec=0)
        root.after(40, redraw)

    root.after(40, redraw)
    root.mainloop()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    build_gui()
