import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan, Imu
from nav_msgs.msg import Odometry
import tkinter as tk
import subprocess
import math
import time
import heapq

CANVAS_SIZE = 520
MAP_MIN, MAP_MAX = -8.0, 8.0
GRID_RES = 0.20
GRID_SIZE = int((MAP_MAX - MAP_MIN) / GRID_RES)

def w2c(x, y):
    cx = (x - MAP_MIN) / (MAP_MAX - MAP_MIN) * CANVAS_SIZE
    cy = CANVAS_SIZE - (y - MAP_MIN) / (MAP_MAX - MAP_MIN) * CANVAS_SIZE
    return cx, cy

def c2w(cx, cy):
    x = MAP_MIN + (cx / CANVAS_SIZE) * (MAP_MAX - MAP_MIN)
    y = MAP_MIN + ((CANVAS_SIZE - cy) / CANVAS_SIZE) * (MAP_MAX - MAP_MIN)
    return x, y

def w2g(x, y):
    gx = int((x - MAP_MIN) / GRID_RES)
    gy = int((y - MAP_MIN) / GRID_RES)
    return max(0, min(GRID_SIZE - 1, gx)), max(0, min(GRID_SIZE - 1, gy))

def g2w(gx, gy):
    x = MAP_MIN + (gx + 0.5) * GRID_RES
    y = MAP_MIN + (gy + 0.5) * GRID_RES
    return x, y

class ShuttleNode(Node):
    def __init__(self):
        super().__init__('shuttle_ignition')
        self.pub_cmd = self.create_publisher(Twist, '/cmd_vel', 10)
        self.sub_odom = self.create_subscription(Odometry, '/odom', self.on_odom, 10)
        self.sub_scan = self.create_subscription(LaserScan, '/scan', self.on_scan, 10)
        self.sub_imu = self.create_subscription(Imu, '/imu', self.on_imu, 10)
        self.sub_cliff_l = self.create_subscription(LaserScan, '/cliff/front_left', self.on_cliff_l, 10)
        self.sub_cliff_r = self.create_subscription(LaserScan, '/cliff/front_right', self.on_cliff_r, 10)

        self.rx, self.ry, self.rz, self.yaw = -5.5, 0.0, 0.08, 0.0
        self.pitch = 0.0
        self.cliff_l = 0.15
        self.cliff_r = 0.15
        self.cliff_alarm = False
        self.cliff_recover_until = 0.0

        self.spawned_cylinders = []
        self.cyl_count = 0
        self.lidar_points = []
        self.min_front_dist = 10.0

        self.state = 'IDLE'
        self.wait_until = 0.0
        self.path = []
        self.last_plan_time = 0.0

        self.create_timer(0.04, self.control_loop)

    def on_odom(self, msg):
        self.rx = msg.pose.pose.position.x
        self.ry = msg.pose.pose.position.y
        self.rz = msg.pose.pose.position.z
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        self.yaw = math.atan2(siny_cosp, cosy_cosp)

    def on_imu(self, msg):
        q = msg.orientation
        sinp = 2.0 * (q.w * q.y - q.z * q.x)
        self.pitch = math.asin(max(-1.0, min(1.0, sinp)))

    def on_cliff_l(self, msg):
        if msg.ranges and not math.isnan(msg.ranges[0]):
            self.cliff_l = msg.ranges[0]

    def on_cliff_r(self, msg):
        if msg.ranges and not math.isnan(msg.ranges[0]):
            self.cliff_r = msg.ranges[0]

    def on_scan(self, msg):
        pts = []
        min_f = 10.0
        angle = msg.angle_min
        for r in msg.ranges:
            if 0.22 < r < 6.0 and not math.isinf(r) and not math.isnan(r):
                wx = self.rx + r * math.cos(self.yaw + angle)
                wy = self.ry + r * math.sin(self.yaw + angle)

                if 1.4 <= wx <= 6.6 and (2.3 <= wy <= 4.7 or -4.7 <= wy <= -2.3):
                    angle += msg.angle_increment
                    continue

                pts.append((wx, wy))
                if r < min_f and abs(angle) < 0.35:
                    min_f = r
            angle += msg.angle_increment

        self.lidar_points = pts[::3]
        self.min_front_dist = min_f

    def build_cost_grid(self):
        grid = [[0.0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

        def add_box(xmin, xmax, ymin, ymax, hard_pad=0.28, soft_pad=0.45):
            gx1, gy1 = w2g(xmin - soft_pad, ymin - soft_pad)
            gx2, gy2 = w2g(xmax + soft_pad, ymax + soft_pad)
            for gx in range(gx1, gx2 + 1):
                for gy in range(gy1, gy2 + 1):
                    wx, wy = g2w(gx, gy)
                    dx = max(xmin - wx, 0.0, wx - xmax)
                    dy = max(ymin - wy, 0.0, wy - ymax)
                    dist = math.hypot(dx, dy)
                    if dist <= hard_pad:
                        grid[gx][gy] = 999.0
                    elif dist <= soft_pad and grid[gx][gy] < 999.0:
                        cost = 10.0 * ((soft_pad - dist) / (soft_pad - hard_pad)) ** 2
                        if cost > grid[gx][gy]:
                            grid[gx][gy] = cost

        add_box(-2.7, -1.7, 1.7, 2.7)
        add_box(-2.7, -1.7, -2.7, -1.7)
        add_box(-0.8, 0.2, -0.4, 0.4)

        add_box(1.5, 6.7, 4.5, 4.8, hard_pad=0.25, soft_pad=0.38)
        add_box(1.5, 6.7, 2.2, 2.5, hard_pad=0.25, soft_pad=0.38)
        add_box(6.5, 6.8, 2.2, 4.8, hard_pad=0.25, soft_pad=0.38)

        add_box(1.5, 6.7, -2.5, -2.2, hard_pad=0.25, soft_pad=0.38)
        add_box(1.5, 6.7, -4.8, -4.5, hard_pad=0.25, soft_pad=0.38)
        add_box(6.5, 6.8, -4.8, -2.2, hard_pad=0.25, soft_pad=0.38)

        for cx, cy in self.spawned_cylinders:
            add_box(cx - 0.23, cx + 0.23, cy - 0.23, cy + 0.23, hard_pad=0.28, soft_pad=0.50)

        return grid

    def smooth_path(self, raw_path, grid):
        if len(raw_path) <= 2:
            return raw_path

        def line_free(p1, p2):
            dist = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            steps = max(2, int(dist / (GRID_RES * 0.4)))
            for i in range(1, steps):
                t = i / float(steps)
                x = p1[0] + t * (p2[0] - p1[0])
                y = p1[1] + t * (p2[1] - p1[1])
                gx, gy = w2g(x, y)
                if grid[gx][gy] >= 5.0:
                    return False
            return True

        smoothed = [raw_path[0]]
        idx = 0
        while idx < len(raw_path) - 1:
            next_idx = idx + 1
            for j in range(len(raw_path) - 1, idx + 1, -1):
                if line_free(raw_path[idx], raw_path[j]):
                    next_idx = j
                    break
            smoothed.append(raw_path[next_idx])
            idx = next_idx

        dense = []
        for i in range(len(smoothed) - 1):
            p1, p2 = smoothed[i], smoothed[i + 1]
            d = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            n = max(1, int(d / 0.15))
            for k in range(n):
                t = k / float(n)
                dense.append((p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])))
        dense.append(smoothed[-1])
        return dense

    def astar(self, start_w, goal_w):
        grid = self.build_cost_grid()
        start = w2g(*start_w)
        goal = w2g(*goal_w)
        grid[start[0]][start[1]] = 0.0
        grid[goal[0]][goal[1]] = 0.0

        open_set = [(0.0, start)]
        came_from = {}
        g_score = {start: 0.0}

        dirs = [(-1,0,1.0),(1,0,1.0),(0,-1,1.0),(0,1,1.0),
                (-1,-1,1.414),(-1,1,1.414),(1,-1,1.414),(1,1,1.414)]

        while open_set:
            _, cur = heapq.heappop(open_set)
            if cur == goal:
                path = []
                while cur in came_from:
                    path.append(g2w(*cur))
                    cur = came_from[cur]
                path.reverse()
                path.append(goal_w)
                return self.smooth_path(path, grid)

            for dx, dy, move_cost in dirs:
                nb = (cur[0] + dx, cur[1] + dy)
                if not (0 <= nb[0] < GRID_SIZE and 0 <= nb[1] < GRID_SIZE):
                    continue
                cell_cost = grid[nb[0]][nb[1]]
                if cell_cost >= 999.0:
                    continue
                tg = g_score[cur] + move_cost + cell_cost
                if tg < g_score.get(nb, 1e9):
                    came_from[nb] = cur
                    g_score[nb] = tg
                    f = tg + math.hypot(nb[0] - goal[0], nb[1] - goal[1])
                    heapq.heappush(open_set, (f, nb))
        return [goal_w]

    def is_cliff_detected(self):
        if self.rz > 0.32 and self.rx > 4.8:
            if self.cliff_l > 0.25 or self.cliff_r > 0.25 or self.cliff_l < 0.10 or self.cliff_r < 0.10:
                return True
            if self.ry > 0:
                if self.rx > 6.30 or self.ry > 4.30 or self.ry < 2.70:
                    return True
            else:
                if self.rx > 6.30 or self.ry > -2.70 or self.ry < -4.30:
                    return True
        return False

    def follow_path(self, target_list, use_astar=True):
        now = time.time()
        final_x, final_y = target_list[-1] if isinstance(target_list, list) else target_list

        if math.hypot(final_x - self.rx, final_y - self.ry) < 0.20:
            return True

        if use_astar:
            if not self.path or (now - self.last_plan_time > 1.0):
                self.path = self.astar((self.rx, self.ry), (final_x, final_y))
                self.last_plan_time = now
            target_path = self.path
        else:
            target_path = [target_list] if not isinstance(target_list, list) else target_list

        lookahead = 0.90
        wp_x, wp_y = target_path[-1]
        for px, py in target_path:
            if math.hypot(px - self.rx, py - self.ry) >= lookahead:
                wp_x, wp_y = px, py
                break

        while len(target_path) > 1 and math.hypot(target_path[0][0] - self.rx, target_path[0][1] - self.ry) < 0.30:
            target_path.pop(0)

        dx = wp_x - self.rx
        dy = wp_y - self.ry
        target_yaw = math.atan2(dy, dx)
        yaw_err = math.atan2(math.sin(target_yaw - self.yaw), math.cos(target_yaw - self.yaw))

        cmd = Twist()
        base_speed = 0.60 if abs(self.pitch) > 0.05 else 0.80

        cmd.linear.x = base_speed * max(0.5, 1.0 - abs(yaw_err) * 0.4)
        cmd.angular.z = max(-1.0, min(1.0, 1.1 * yaw_err))

        self.pub_cmd.publish(cmd)
        return False

    def drive_ramp_straight(self, target_x, center_y):
        """Прямолинейный заход и движение по пандусу без пересчета сетки (абсолютно ровно)"""
        dx = target_x - self.rx
        dy_to_center = center_y - self.ry
        
        if abs(dx) < 0.15:
            return True

        desired_yaw = 0.0 if dx > 0 else math.pi
        # Жесткая коррекция по оси Y пандуса
        y_corr = max(-0.25, min(0.25, 1.2 * dy_to_center))
        if dx > 0:
            desired_yaw += y_corr
        else:
            desired_yaw -= y_corr

        yaw_err = math.atan2(math.sin(desired_yaw - self.yaw), math.cos(desired_yaw - self.yaw))

        cmd = Twist()
        if abs(yaw_err) > 0.20:
            cmd.linear.x = 0.0
            cmd.angular.z = max(-1.2, min(1.2, 2.0 * yaw_err))
        else:
            cmd.linear.x = 0.45 if abs(self.pitch) > 0.05 else 0.65
            cmd.angular.z = max(-0.8, min(0.8, 1.5 * yaw_err))

        self.pub_cmd.publish(cmd)
        return False

    def control_loop(self):
        now = time.time()
        cmd = Twist()

        if self.is_cliff_detected():
            self.cliff_alarm = True
            self.cliff_recover_until = now + 1.0

        if now < self.cliff_recover_until:
            cmd.linear.x = -0.35
            cmd.angular.z = 0.0
            self.pub_cmd.publish(cmd)
            return
        else:
            self.cliff_alarm = False

        if self.state == 'IDLE':
            self.path = []
            self.pub_cmd.publish(cmd)
            return

        if self.state == 'TO_APPROACH_A':
            if self.follow_path((1.0, 3.5), use_astar=True):
                self.state = 'CLIMB_A'
        elif self.state == 'CLIMB_A':
            if self.drive_ramp_straight(5.5, 3.5):
                self.state = 'WAIT_A'
                self.wait_until = now + 2.0
        elif self.state == 'WAIT_A':
            self.pub_cmd.publish(cmd)
            if now >= self.wait_until:
                self.state = 'DESCEND_A'
        elif self.state == 'DESCEND_A':
            if self.drive_ramp_straight(1.0, 3.5):
                self.state = 'TO_APPROACH_B'

        elif self.state == 'TO_APPROACH_B':
            if self.follow_path((1.0, -3.5), use_astar=True):
                self.state = 'CLIMB_B'
        elif self.state == 'CLIMB_B':
            if self.drive_ramp_straight(5.5, -3.5):
                self.state = 'WAIT_B'
                self.wait_until = now + 2.0
        elif self.state == 'WAIT_B':
            self.pub_cmd.publish(cmd)
            if now >= self.wait_until:
                self.state = 'DESCEND_B'
        elif self.state == 'DESCEND_B':
            if self.drive_ramp_straight(1.0, -3.5):
                self.state = 'TO_APPROACH_A'

        elif self.state == 'TO_HOME':
            if self.rx > 1.2 and abs(self.ry) > 2.0:
                cy = 3.5 if self.ry > 0 else -3.5
                self.drive_ramp_straight(1.0, cy)
            else:
                if self.follow_path((-5.5, 0.0), use_astar=True):
                    self.state = 'IDLE'
                    self.pub_cmd.publish(cmd)

    def spawn_cylinder(self, wx, wy):
        self.cyl_count += 1
        name = f"cyl_{self.cyl_count}"
        self.spawned_cylinders.append((wx, wy))
        self.last_plan_time = 0.0
        sdf = (
            f"<sdf version='1.8'><model name='{name}'><static>true</static>"
            f"<pose>{wx:.2f} {wy:.2f} 0.5 0 0 0</pose>"
            f"<link name='l'><collision name='c'><geometry><cylinder><radius>0.23</radius><length>1.0</length></cylinder></geometry></collision>"
            f"<visual name='v'><geometry><cylinder><radius>0.23</radius><length>1.0</length></cylinder></geometry>"
            f"<material><ambient>0.9 0.15 0.15 1</ambient><diffuse>0.9 0.15 0.15 1</diffuse></material></visual></link></model></sdf>"
        )
        subprocess.Popen([
            'ign', 'service', '-s', '/world/warehouse_world/create',
            '--reqtype', 'ignition.msgs.EntityFactory',
            '--reptype', 'ignition.msgs.Boolean',
            '--timeout', '1000',
            '--req', f'sdf: "{sdf}"'
        ])

def main():
    rclpy.init()
    node = ShuttleNode()

    root = tk.Tk()
    root.title("Пульт управления челноком (Прямолинейный заход на пандус)")
    root.resizable(False, False)

    bf = tk.Frame(root)
    bf.pack(fill="x", padx=10, pady=6)

    def start_cmd():
        if node.rx > 1.5 and node.ry > 2.0:
            node.state = 'CLIMB_A'
        elif node.rx > 1.5 and node.ry < -2.0:
            node.state = 'CLIMB_B'
        else:
            node.state = 'TO_APPROACH_A'

    btn_start = tk.Button(
        bf, text="▶ СТАРТ (ДОМ ⇄ Куб А ⇄ Куб Б)",
        bg="#27ae60", fg="white", font=("Arial", 11, "bold"), height=2,
        command=start_cmd
    )
    btn_start.pack(fill="x", pady=2)

    sub_f = tk.Frame(bf)
    sub_f.pack(fill="x", pady=2)

    btn_home = tk.Button(
        sub_f, text="🏠 ДОМОЙ",
        bg="#2980b9", fg="white", font=("Arial", 10, "bold"), height=1,
        command=lambda: setattr(node, 'state', 'TO_HOME')
    )
    btn_home.pack(side="left", padx=2, expand=True, fill="x")

    btn_stop = tk.Button(
        sub_f, text="⏹ СТОП",
        bg="#c0392b", fg="white", font=("Arial", 10, "bold"), height=1,
        command=lambda: setattr(node, 'state', 'IDLE')
    )
    btn_stop.pack(side="right", padx=2, expand=True, fill="x")

    status_lbl = tk.Label(root, text="Клик мыши по карте = спавн цилиндра", font=("Arial", 9))
    status_lbl.pack()

    cv = tk.Canvas(root, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="#f5f6fa", highlightthickness=1, highlightbackground="#7f8c8d")
    cv.pack(padx=10, pady=5)
    cv.bind("<Button-1>", lambda e: node.spawn_cylinder(*c2w(e.x, e.y)))

    def redraw():
        cv.delete("all")

        for coord in range(-6, 8, 2):
            x1, y1 = w2c(coord, -8)
            x2, y2 = w2c(coord, 8)
            cv.create_line(x1, y1, x2, y2, fill="#dcdde1")
            x1, y1 = w2c(-8, coord)
            x2, y2 = w2c(8, coord)
            cv.create_line(x1, y1, x2, y2, fill="#dcdde1")

        for ox, oy in [(-2.2, 2.2), (-2.2, -2.2), (-0.5, 0.0)]:
            x1, y1 = w2c(ox - 0.5, oy + 0.5)
            x2, y2 = w2c(ox + 0.5, oy - 0.5)
            cv.create_rectangle(x1, y1, x2, y2, fill="#a0522d", outline="black")

        hx1, hy1 = w2c(-6.3, 0.8)
        hx2, hy2 = w2c(-4.7, -0.8)
        cv.create_rectangle(hx1, hy1, hx2, hy2, fill="#2ecc71", outline="black", width=2)
        cv.create_text(*w2c(-5.5, 0.0), text="ДОМ", font=("Arial", 9, "bold"))

        cv.create_rectangle(*w2c(1.6, 4.6), *w2c(4.4, 2.4), fill="#95a5a6", outline="black")
        cv.create_text(*w2c(3.0, 3.5), text="Пандус А →", font=("Arial", 8, "bold"))
        cv.create_rectangle(*w2c(4.4, 4.6), *w2c(6.6, 2.4), fill="#3498db", outline="black", width=2)
        cv.create_text(*w2c(5.5, 3.5), text="КУБ А\n(0.40м)", fill="white", font=("Arial", 9, "bold"))

        cv.create_rectangle(*w2c(1.6, -2.4), *w2c(4.4, -4.6), fill="#95a5a6", outline="black")
        cv.create_text(*w2c(3.0, -3.5), text="Пандус Б →", font=("Arial", 8, "bold"))
        cv.create_rectangle(*w2c(4.4, -2.4), *w2c(6.6, -4.6), fill="#e67e22", outline="black", width=2)
        cv.create_text(*w2c(5.5, -3.5), text="КУБ Б\n(0.40м)", fill="white", font=("Arial", 9, "bold"))

        for cx, cy in node.spawned_cylinders:
            px, py = w2c(cx, cy)
            cv.create_oval(px-8, py-8, px+8, py+8, fill="#e74c3c", outline="black")

        for lx, ly in node.lidar_points:
            px, py = w2c(lx, ly)
            cv.create_oval(px-1, py-1, px+1, py+1, fill="#8e44ad", outline="")

        if len(node.path) > 0:
            pts = [*w2c(node.rx, node.ry)]
            for px, py in node.path:
                pts.extend(w2c(px, py))
            if len(pts) >= 4:
                cv.create_line(*pts, fill="#8e44ad", width=2, smooth=True)

        rx, ry = w2c(node.rx, node.ry)
        col = "#e74c3c" if node.cliff_alarm else "#00cec9"
        cv.create_oval(rx-10, ry-10, rx+10, ry+10, fill=col, outline="black", width=2)
        nx = rx + 15 * math.cos(node.yaw)
        ny = ry - 15 * math.sin(node.yaw)
        cv.create_line(rx, ry, nx, ny, fill="black", width=3)

        status_lbl.config(
            text=f"Режим: {node.state} | Коорд: ({node.rx:.1f}, {node.ry:.1f}, Z={node.rz:.2f}м)"
        )

        if node.cliff_alarm:
            cv.create_text(CANVAS_SIZE//2, 20, text="⚠ ЭКСТРЕННЫЙ СТОП: КРАЙ ПЛАТФОРМЫ!", fill="red", font=("Arial", 11, "bold"))

        rclpy.spin_once(node, timeout_sec=0)
        root.after(40, redraw)

    root.after(40, redraw)
    root.mainloop()
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
