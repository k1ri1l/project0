#include "rclcpp/rclcpp.hpp"
#include "geometry_msgs/msg/point.hpp"
#include "std_msgs/msg/bool.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "ros_gz_interfaces/msg/contacts.hpp"
#include <GLFW/glfw3.h>
#include "imgui/imgui.h"
#include "imgui/imgui_impl_glfw.h"
#include "imgui/imgui_impl_opengl3.h"
#include <atomic>
#include <mutex>
#include <opencv2/opencv.hpp>
#include "cv_bridge/cv_bridge.hpp"
GLFWwindow* window = nullptr;
std::shared_ptr<rclcpp::Publisher<geometry_msgs::msg::Point>> publisher = nullptr;
std::shared_ptr<rclcpp::Publisher<geometry_msgs::msg::Point>> publisher_povorot = nullptr;
std::shared_ptr<rclcpp::Publisher<geometry_msgs::msg::Point>> publisher_zahvat = nullptr;
std::shared_ptr<rclcpp::Publisher<geometry_msgs::msg::Point>> publisher_position = nullptr;
std::shared_ptr<rclcpp::Publisher<std_msgs::msg::Bool>> publisher_conveyer = nullptr;
rclcpp::Subscription<ros_gz_interfaces::msg::Contacts>::SharedPtr subscription = nullptr;
rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr subscription_ = nullptr;
std::shared_ptr<rclcpp::Node> node = nullptr;
static float position[2] ={0.0f,0.0f};
bool was_rgt_pressed = false;
bool was_lft_pressed = false;
bool was_fwd_pressed = false;
bool was_bwd_pressed = false;
bool was_up_pressed = false;
bool was_dwn_pressed = false;
bool was_possition= false;
bool was_zahvat= false;
bool was_conveyer= false;
std::atomic<bool> is_touched{false};
std::atomic<double> last_touch_time{0.0};
std::atomic<int64_t> last_touch_nanoseconds{0}; 

struct SharedTextureData {
    cv::Mat frame;       
    std::mutex mutex;    
    bool is_new = false; 
};

SharedTextureData shared_data; 
cv::Mat frame; 
GLuint camera_texture_id = 0; 
cv::Mat local_frame;          \
bool update_gpu = false;

void UploadFrameToGPU(const cv::Mat& input_mat, GLuint& texture_id) {
    if (input_mat.empty()) return;
    if (texture_id == 0) {
        glGenTextures(1, &texture_id);
    }
    glBindTexture(GL_TEXTURE_2D, texture_id);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    cv::Mat rgba_mat;
    cv::cvtColor(input_mat, rgba_mat, cv::COLOR_BGR2RGBA);
    glTexImage2D(
        GL_TEXTURE_2D, 
        0, 
        GL_RGBA, 
        rgba_mat.cols,     
        rgba_mat.rows,     
        0, 
        GL_RGBA, 
        GL_UNSIGNED_BYTE,  
        rgba_mat.data      
    );
    glBindTexture(GL_TEXTURE_2D, 0);
}

void init(){
glfwInit();
glfwWindowHint(GLFW_FOCUS_ON_SHOW, GLFW_FALSE);
glfwWindowHint(GLFW_FLOATING, GLFW_TRUE);
glfwWindowHint(GLFW_RESIZABLE, GLFW_FALSE);

window = glfwCreateWindow(1000,500,"Панель управления", NULL,NULL);
glfwSetWindowAttrib(window, GLFW_FLOATING, GLFW_TRUE);
glfwMakeContextCurrent(window);
glfwSwapInterval(2);

IMGUI_CHECKVERSION();
ImGui::CreateContext();
ImGuiIO& io = ImGui::GetIO();
io.Fonts->AddFontFromFileTTF("/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf", 16.0f, NULL, io.Fonts->GetGlyphRangesCyrillic());
ImGui_ImplGlfw_InitForOpenGL(window,true);
ImGui_ImplOpenGL3_Init("#version 130");
rclcpp::init(0, nullptr);
node = rclcpp::Node::make_shared("gui_panel_node");

publisher = node->create_publisher<geometry_msgs::msg::Point>("gui_commands", 10);
publisher_povorot = node->create_publisher<geometry_msgs::msg::Point>("gui_commands_povorot", 10);
publisher_zahvat = node->create_publisher<geometry_msgs::msg::Point>("gui_commands_zahvat", 10);
publisher_position = node->create_publisher<geometry_msgs::msg::Point>("gui_commands_position", 10);
publisher_conveyer = node->create_publisher<std_msgs::msg::Bool>("gui_commands_conveyer", 10);
subscription = node->create_subscription<ros_gz_interfaces::msg::Contacts>(
    "/world/manipulator_V2/model/box4/link/zahvat/sensor/left_finger_contact/contact", 
    10, 
    [](const ros_gz_interfaces::msg::Contacts::SharedPtr msg) {
    if (!msg->contacts.empty()) {
            last_touch_nanoseconds.store(node->now().nanoseconds());
            is_touched.store(true);
        }});
subscription_ = node->create_subscription<sensor_msgs::msg::Image>(
    "/camera/image_raw", 
    10,
    [](const sensor_msgs::msg::Image::SharedPtr msg) {
        std::lock_guard<std::mutex> lock(shared_data.mutex);
        try {
            shared_data.frame = cv_bridge::toCvCopy(msg, "bgr8")->image;
            shared_data.is_new = true;
        } 
        catch (cv_bridge::Exception& e) {
            RCLCPP_ERROR(rclcpp::get_logger("rclcpp"), "Ошибка cv_bridge: %s", e.what());
        }
    }
);
}


void control(){
  auto now_ns = node->now().nanoseconds();
  if (now_ns - last_touch_nanoseconds.load() > 100000000) {
      is_touched.store(false);
  }
  ImGui::Begin("управление мостовым краном");
  ImGui::Button("вправо", ImVec2(150, 50)); bool is_rgt_active = ImGui::IsItemActive(); 
  ImGui::Button("влево", ImVec2(150, 50)); bool is_lft_active = ImGui::IsItemActive(); 
  ImGui::Button("вперед", ImVec2(150, 50)); bool is_fwd_active = ImGui::IsItemActive(); 
  ImGui::Button("назад", ImVec2(150, 50)); bool is_bwd_active = ImGui::IsItemActive();
  ImGui::Button("вверх", ImVec2(150, 50)); bool is_up_active = ImGui::IsItemActive(); 
  ImGui::Button("вниз", ImVec2(150, 50)); bool is_dwn_active = ImGui::IsItemActive();
  if (is_fwd_active){
    geometry_msgs::msg::Point msg1;
    msg1.x = 6.0;
    publisher->publish(msg1);
    was_fwd_pressed = true;
    was_bwd_pressed = false;
  }else if(is_bwd_active){
    geometry_msgs::msg::Point msg1;
    msg1.x = -6.0;
    publisher->publish(msg1);
    was_bwd_pressed = true;
    was_fwd_pressed = false;
  }else if (was_fwd_pressed || was_bwd_pressed){
    geometry_msgs::msg::Point msg1;
    msg1.x = 0.0;
    publisher->publish(msg1);
    was_fwd_pressed = false;
    was_bwd_pressed = false;
  }
  if (is_rgt_active){
    geometry_msgs::msg::Point msg1;
    msg1.y = 6.0;
    publisher->publish(msg1);
    was_rgt_pressed = true;
    was_lft_pressed = false;
  }else if(is_lft_active){
    geometry_msgs::msg::Point msg1;
    msg1.y = -6.0;
    publisher->publish(msg1);
    was_lft_pressed = true;
    was_rgt_pressed = false;
  }else if (was_rgt_pressed || was_lft_pressed){
    geometry_msgs::msg::Point msg1;
    msg1.y = 0.0;
    publisher->publish(msg1);
    was_rgt_pressed = false;
    was_lft_pressed = false;
  }
  if (is_up_active){
    geometry_msgs::msg::Point msg1;
    msg1.z = 3.0;
    publisher->publish(msg1);
    was_up_pressed = true;
    was_dwn_pressed = false;
  }else if(is_dwn_active){
    geometry_msgs::msg::Point msg1;
    msg1.z = -3.0;
    publisher->publish(msg1);
    was_dwn_pressed = true;
    was_up_pressed = false;
  }else if (was_dwn_pressed || was_up_pressed){
    geometry_msgs::msg::Point msg1;
    msg1.z = 0.0;
    publisher->publish(msg1);
    was_up_pressed = false;
    was_dwn_pressed = false;
  }
  ImGui::End();
  ImGui::Begin("управление захватом");
  if(ImGui::Button("переворот", ImVec2(150,50))){
    geometry_msgs::msg::Point msg2;
    if(was_possition){
      msg2.z = 1.0;
      was_possition = false;
    }else{
      msg2.z = 0.0;
      was_possition = true;
    }
    publisher_povorot->publish(msg2);
  }
  if(is_touched.load()){
    ImGui::TextColored(ImVec4(0, 1, 0, 1), "КОНТАКТ ЕСТЬ!");
  }else{
    ImGui::Text("нет косания");
  }
  if(ImGui::Button("захват", ImVec2(150,50))){
      geometry_msgs::msg::Point msg3;
    if(was_zahvat){

      msg3.y = 0.0;
      was_zahvat = false;
    }else{
      if(is_touched.load()){
      msg3.y = 1.0;
      was_zahvat = true;
      }
    }
    publisher_zahvat->publish(msg3);
  }
  ImGui::InputFloat2("координаты XY",position);
  ImGui::Spacing();
  if(ImGui::Button("переместить в точку")){
    geometry_msgs::msg::Point msg4;
    msg4.x = position[0];
    msg4.y = position[1];
    publisher_position->publish(msg4);
    RCLCPP_INFO(node->get_logger(), "отправлено!");
  }
  if(ImGui::Button("запустить/остановить конвейер", ImVec2(150,50))){
    std_msgs::msg::Bool msg5;
    was_conveyer = !was_conveyer;
    msg5.data = was_conveyer;
    publisher_conveyer->publish(msg5);
  }

  ImGui::End();
  
  
}
void camera(){
  
  {
        std::lock_guard<std::mutex> lock(shared_data.mutex);
        if (shared_data.is_new) {
            shared_data.frame.copyTo(local_frame); 
            shared_data.is_new = false;
            update_gpu = true; 
        }
    }
  if (update_gpu && !local_frame.empty()) {
        UploadFrameToGPU(local_frame, camera_texture_id);
        update_gpu = false;
    }

  ImGui::Begin("Камера Gazebo");
  if (camera_texture_id != 0) {
        void* texture_ptr = reinterpret_cast<void*>(static_cast<intptr_t>(camera_texture_id));
        ImVec2 image_size(static_cast<float>(local_frame.cols), static_cast<float>(local_frame.rows));

        ImGui::Image(texture_ptr, image_size);
    } else {
        ImGui::Text("Ожидание сигнала от камеры...");
    }

    ImGui::End();
    
}

int main(int argc, char** argv){
init();

while (!glfwWindowShouldClose(window)){
  glfwPollEvents();
  rclcpp::spin_some(node);
  ImGui_ImplOpenGL3_NewFrame();
  ImGui_ImplGlfw_NewFrame();
  ImGui::NewFrame();
  control();
  camera();
  ImGui::Render();
  int display_w, display_h;
        glfwGetFramebufferSize(window, &display_w, &display_h);
        glViewport(0, 0, display_w, display_h);
        glClearColor(0.2f, 0.2f, 0.2f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
        glfwSwapBuffers(window);
}
ImGui_ImplOpenGL3_Shutdown();
ImGui_ImplGlfw_Shutdown();
ImGui::DestroyContext();
glfwDestroyWindow(window);
glfwTerminate();
rclcpp::shutdown();
return 0;
}
