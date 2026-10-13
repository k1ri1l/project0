#include <opencv2/opencv.hpp>
#include "cv_bridge/cv_bridge.hpp"
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/bool.hpp"
#include "std_msgs/msg/string.hpp"
#include "geometry_msgs/msg/point.hpp"
#include <mutex>
#include <atomic>
#include <fstream>
#include <chrono>

static float position[2] ={0.0f,0.0f};

std::shared_ptr<rclcpp::Node> node = nullptr;
rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher = nullptr;
rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr subscription = nullptr;
rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr subscription_ = nullptr;
std::shared_ptr<rclcpp::Publisher<geometry_msgs::msg::Point>> publisher_position = nullptr;
std::shared_ptr<rclcpp::Publisher<sensor_msgs::msg::Image>> publisher_mask = nullptr;
struct SharedTextureData {
    cv::Mat frame;       
    std::mutex mutex;    
    bool is_new = false; 
};
struct passports{
  double solidity;
  double curcularity;
  double end_points_count[2];
  std::string name;
};
std::string  predict;
std::vector<passports> passport_base;
SharedTextureData shared_data; 
cv::Mat frame; 
cv::Mat local_frame;          
bool update_gpu = false;
bool colib = false;
bool k = true;
double ky =0.1;
double kx = 0.1;

void init(){
    
    std::ifstream in("/home/valerii/proect_manipulator/src/proect_manipulator/passports.txt");
    if(!in.is_open()){
      return;
    }
    passports temp;
    
    while (in >> temp.solidity >> temp.curcularity >> temp.end_points_count[0] >> temp.end_points_count[1] >> temp.name) {
        passport_base.push_back(temp);
    }
    in.close();
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
    subscription = node->create_subscription<std_msgs::msg::Bool>(
    "colib", 
    10, 
    [](const std_msgs::msg::Bool::SharedPtr msg2) {
        if(msg2->data == true){
           colib = true;
        }
    }
    );
    publisher = node->create_publisher<std_msgs::msg::String>("gui_commands_predict",10);
    publisher_position = node->create_publisher<geometry_msgs::msg::Point>("gui_commands_position",10);
    publisher_mask = node->create_publisher<sensor_msgs::msg::Image>("/camera/image_mask", 10);
}

void raspoznovanie(){
  std::vector<std::vector<cv::Point>> vector;
  cv::Mat HSV_frame;
  cv::Mat mask;
  cv::Mat kernel = cv::getStructuringElement(cv::MORPH_RECT, cv::Size(3, 3));
  cv::medianBlur(frame,frame,5);
  cv::cvtColor(frame, HSV_frame, cv::COLOR_BGR2HSV);
  cv::inRange(HSV_frame, cv::Scalar(35, 30, 40), cv::Scalar(85, 255, 255), mask);  cv::morphologyEx(mask, mask, cv::MORPH_OPEN, kernel);
  cv::morphologyEx(mask, mask, cv::MORPH_CLOSE, kernel);
  cv::findContours(mask,vector,cv::RETR_EXTERNAL,cv::CHAIN_APPROX_SIMPLE);
  if (!mask.empty()) {
    auto mask_msg = cv_bridge::CvImage(std_msgs::msg::Header(), "mono8", mask).toImageMsg();
    publisher_mask->publish(*mask_msg);
  }
  if(vector.empty()){
    std_msgs::msg::String msg_predict;
    msg_predict.data = "поиск...";
    publisher->publish(msg_predict);
    return;
  }
  
  int length = vector.size(); double max_area = -1.0; int best=0;
  for(int i = 0; i <length;i++){
    double area = contourArea(vector[i]);
    if(max_area<area){best = i; max_area = area;}
  }
  std::vector<cv::Point> best_vector = vector[best];
  double area =contourArea(best_vector);
  std::vector<cv::Point> null;
  cv::convexHull(best_vector,null);
    
  double area_null = contourArea(null);
  double solidity = 0.0;
  if(area_null >0){
      solidity = area/area_null;
  }
  double peremiter = cv::arcLength(best_vector,true);
  double curcularity = 0.0;
  if(peremiter >0){
      curcularity = (4.0 * M_PI * area)/(peremiter * peremiter);
  }
  cv::Mat skeleton = cv::Mat::zeros(mask.size(), CV_8UC1);
  cv::Mat element = cv::getStructuringElement(cv::MORPH_CROSS, cv::Size(3, 3));
  cv::Mat img = mask.clone();
  cv::Mat temp;
  cv::Mat eroded;
  while (true) {
    cv::erode(img,eroded,element);
    cv::dilate(eroded,temp,element);
    cv::subtract(img,temp,temp);
    cv::bitwise_or(skeleton,temp,skeleton);
    img = eroded.clone();
    if(cv::countNonZero(img)==0){
      break;
    }
  }
  int end_points_count = 0;
  for (int y = 1; y < skeleton.rows - 1; y++) {
    for (int x = 1; x < skeleton.cols - 1; x++) {
      if (skeleton.at<uchar>(y, x) > 0) {
        int neighbors = 0;
        for (int ky = -1; ky <= 1; ky++) {
            for (int kx = -1; kx <= 1; kx++) {
                if (ky == 0 && kx == 0) continue;
                if (skeleton.at<uchar>(y + ky, x + kx) > 0) {
                    neighbors++;
                }
            }
        }
        if (neighbors == 1) {
            end_points_count++;
        }
      }
  
    }
  }
  int length1 =passport_base.size();
  int best1 = 0;
  double best_error = 999.0;
  for(int i=0;i<length1; i++){
    double error_solidity = std::abs(solidity - passport_base[i].solidity);
    double error_curcularity = std::abs(curcularity - passport_base[i].curcularity);
    double error_end_points;
    if(passport_base[i].end_points_count[0] <= end_points_count && end_points_count <= passport_base[i].end_points_count[1] ){
      error_end_points =0.0;
    }else if(passport_base[i].end_points_count[0] > end_points_count){
      error_end_points = std::abs(end_points_count - passport_base[i].end_points_count[0]) * 0.25;
    }else{
      error_end_points = std::abs(end_points_count - passport_base[i].end_points_count[1]) * 0.25;
    }
    double error = error_solidity + error_curcularity + error_end_points;
    if(i != 0){
      if(best_error > error){
        best_error =error;
        best1 = i;
      }
    }else{
      best1 =0;
      best_error = error;
    }
  }
  predict = passport_base[best1].name;
  RCLCPP_INFO(node->get_logger(),"найдено: %s", predict.c_str());
  RCLCPP_INFO(node->get_logger(),"с ошибкой %f", best_error);
  RCLCPP_INFO(node->get_logger(),"выпуклость %f", solidity);
  RCLCPP_INFO(node->get_logger(),"округлость %f", curcularity);
  RCLCPP_INFO(node->get_logger(),"концы скелета %d", end_points_count);
  std_msgs::msg::String predict_msg;
  predict_msg.data = predict;
  publisher->publish(predict_msg);
  cv::Moments moment = cv::moments(best_vector);
  double cX = 0.0;
  double cY = 0.0;
  if (moment.m00 != 0.0) {
    cX = (moment.m10 / moment.m00) * kx;
    cY = (moment.m01 / moment.m00) * ky;
  }
  geometry_msgs::msg::Point msg_pos;
  msg_pos.x = cX;
  msg_pos.y = cY;
  if(!k){
    publisher_position->publish(msg_pos);
    k =false;
    RCLCPP_INFO(node->get_logger(),"x %f", cX);
    RCLCPP_INFO(node->get_logger(),"y %f", cY);
  }
}



int main(int argc, char * argv[]){
  rclcpp::init(argc,argv);
  node = rclcpp::Node::make_shared("vision_processor");
  init();
  while(rclcpp::ok()){
    bool process = false;
    rclcpp::spin_some(node);
    {
    std::lock_guard<std::mutex> lock(shared_data.mutex);
      if(shared_data.is_new){
         frame = shared_data.frame.clone();
         process = true;
         shared_data.is_new = false;
      }
    }
    if(process){
    raspoznovanie();}
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
  }
  rclcpp::shutdown();
  return 0;
}