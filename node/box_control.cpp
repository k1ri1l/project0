#include <chrono>
#include <functional>
#include <memory>

#include "rclcpp/rclcpp.hpp"
#include "geometry_msgs/msg/twist.hpp"
#include "geometry_msgs/msg/point.hpp"
#include "std_msgs/msg/float64.hpp"
#include "std_msgs/msg/empty.hpp"
#include "std_msgs/msg/bool.hpp"
float position_m[2] = {5.0,1.73};
float speed[2] = {6.0,6.0};

using namespace std::chrono_literals;

class CubeController : public rclcpp::Node
{
public:
  CubeController() : Node("cube_controller_node"){
    publisher_ =this->create_publisher<geometry_msgs::msg::Twist>("/cmd_vel", 10);
    publisher_y =this->create_publisher<geometry_msgs::msg::Twist>("/cmd_vel_y", 10);
    publisher_z =this->create_publisher<geometry_msgs::msg::Twist>("/cmd_vel_z", 10);
    publisher_rotate =this->create_publisher<std_msgs::msg::Float64>("/cmd_vel_rotate", 10);
    publisher_conveyer =this->create_publisher<std_msgs::msg::Float64>("/conveyor/cmd_vel", 10);
    publisher_zahvat =this->create_publisher<std_msgs::msg::Empty>("/gripper/attach",10);
    publisher_rashvat =this->create_publisher<std_msgs::msg::Empty>("/gripper/detach",10);
    subscription_ = this->create_subscription<geometry_msgs::msg::Point>(
    "gui_commands", 10, std::bind(&CubeController::command_callback, this, std::placeholders::_1));
    subscription_2 = this->create_subscription<geometry_msgs::msg::Point>(
    "gui_commands_povorot", 10, std::bind(&CubeController::povorot, this, std::placeholders::_1));
    subscription_3 = this->create_subscription<geometry_msgs::msg::Point>(
    "gui_commands_zahvat", 10, std::bind(&CubeController::zahvat, this, std::placeholders::_1));
    subscription_4 = this->create_subscription<geometry_msgs::msg::Point>(
    "gui_commands_position", 10, std::bind(&CubeController::position, this, std::placeholders::_1));
    subscription_5 = this->create_subscription<std_msgs::msg::Bool>(
    "gui_commands_conveyer", 10, std::bind(&CubeController::conveyer, this, std::placeholders::_1));
    release_timer_ = this->create_wall_timer(500ms, [this]() {
        auto empty_msg = std_msgs::msg::Empty();
        this->publisher_rashvat->publish(empty_msg);
        RCLCPP_INFO(this->get_logger(), "Автоматический стартовый сброс захвата выполнен.");
        this->release_timer_->cancel(); 
    });
  }
private: 

 
void command_callback(const geometry_msgs::msg::Point::SharedPtr msg){
  if(msg->x != 0.0){
  auto twist_msg = geometry_msgs::msg::Twist();  
  twist_msg.linear.x = msg->x;
  publisher_->publish(twist_msg);
  publisher_z->publish(twist_msg);
  RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "Получено число: %f", msg->x);
  }
  else if(msg->y != 0.0){
  auto twist_msg_y = geometry_msgs::msg::Twist();
  twist_msg_y.linear.y = msg->y;
  publisher_->publish(twist_msg_y);
  publisher_y->publish(twist_msg_y);
  publisher_z->publish(twist_msg_y);
  RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "Получено число: %f", msg->y);
  }else if(msg->z != 0.0){
  auto twist_msg_z = geometry_msgs::msg::Twist();
  twist_msg_z.linear.z = msg->z;
  publisher_z->publish(twist_msg_z);
  RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "Получено число: %f", msg->z);
  }else{
    auto stop_msg = geometry_msgs::msg::Twist(); 
    publisher_z->publish(stop_msg);
    publisher_y->publish(stop_msg);
    publisher_->publish(stop_msg);
    RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "кубы остановились");
  }
}
void povorot(const geometry_msgs::msg::Point::SharedPtr msg2){
  if(msg2->z == 1){
    auto rotate = std_msgs::msg::Float64();  
     rotate.data = 0;
    publisher_rotate->publish(rotate);
    RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "поворот захватом");
  }else if(msg2->z == 0){
    auto rotate = std_msgs::msg::Float64();  
     rotate.data = 3.1416;
    publisher_rotate->publish(rotate);
    RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "поворот гравирователем");
  }
}
void zahvat(const geometry_msgs::msg::Point::SharedPtr msg3){

  if(msg3->y == 1){
    auto empty_msg = std_msgs::msg::Empty();
    publisher_zahvat->publish(empty_msg);
    RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "захватил");
  }else if(msg3->y == 0){
    auto empty_msg = std_msgs::msg::Empty();
    
    publisher_rashvat->publish(empty_msg);
    RCLCPP_INFO(rclcpp::get_logger("rclcpp"), "расхватил");
  }
}
void position(const geometry_msgs::msg::Point::SharedPtr msg4){
  RCLCPP_INFO(this->get_logger(), "пришли данные {msg4->x} {msg4->y}");
  auto msg_target_x = geometry_msgs::msg::Twist();
  auto msg_target_y = geometry_msgs::msg::Twist();
  auto stop_msg = geometry_msgs::msg::Twist();
  float rz_y = std::abs(position_m[1]-msg4->y);
  float np_y = (position_m[1] < msg4->y) ? 1.0f : -1.0f;
  float time_y = (speed[1] > 0) ? (rz_y / speed[1]) : 0.0f;
  int ms_y = static_cast<int>(time_y * 1000.0f);

  float rz_x = std::abs(position_m[0]-msg4->x);
  float np_x = (position_m[0] < msg4->x) ? 1.0f : -1.0f;
  float time_x = (speed[0] > 0) ? (rz_x / speed[0]) : 0.0f;
  int ms_x = static_cast<int>(time_x * 1000.0f);
  if(ms_y > 0){
    RCLCPP_INFO(this->get_logger(), "движение по y");
    msg_target_y.linear.y = speed[1] * np_y;
    publisher_y->publish(msg_target_y);
    rclcpp::sleep_for(std::chrono::milliseconds(ms_y));
    publisher_y->publish(stop_msg);
    RCLCPP_INFO(this->get_logger(), "движение остановлено");
    position_m[1] = msg4->y;
  }
  rclcpp::sleep_for(std::chrono::milliseconds(100));
  if(ms_x > 0){
    RCLCPP_INFO(this->get_logger(), "движение по x");
    msg_target_x.linear.x = speed[0] * np_x;
    publisher_->publish(msg_target_x);
    rclcpp::sleep_for(std::chrono::milliseconds(ms_x));
    publisher_->publish(stop_msg);
    RCLCPP_INFO(this->get_logger(), "движение остановлено");
    position_m[0] = msg4->x;
  }
}
void conveyer(const std_msgs::msg::Bool::SharedPtr msg5){
  if(msg5->data){
    auto speed = std_msgs::msg::Float64(); 
    speed.data = 1.0;
    publisher_conveyer->publish(speed);
    RCLCPP_INFO(this->get_logger(), "конвейер запушен");
  }else{
    auto speed = std_msgs::msg::Float64(); 
    speed.data = 0.0;
    publisher_conveyer->publish(speed);
    RCLCPP_INFO(this->get_logger(), "конвейер остановлен");
  }
  
}
rclcpp::TimerBase::SharedPtr release_timer_;
rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr subscription_5;
rclcpp::Subscription<geometry_msgs::msg::Point>::SharedPtr subscription_4;
rclcpp::Subscription<geometry_msgs::msg::Point>::SharedPtr subscription_3;
rclcpp::Subscription<geometry_msgs::msg::Point>::SharedPtr subscription_2;
rclcpp::Subscription<geometry_msgs::msg::Point>::SharedPtr subscription_;
rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr publisher_;
rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr publisher_y;
rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr publisher_z;
rclcpp::Publisher<std_msgs::msg::Float64>::SharedPtr publisher_rotate;
rclcpp::Publisher<std_msgs::msg::Float64>::SharedPtr publisher_conveyer;
rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr publisher_zahvat;
rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr publisher_rashvat;
};

int main(int argc, char * argv[])
{
  rclcpp::init(argc,argv);
  rclcpp::spin(std::make_shared<CubeController>());
  rclcpp::shutdown();
  return 0;
}
