cmake_minimum_required(VERSION 3.8)
project(proect_manipulator)

set(OpenGL_GL_PREFERENCE "GLVND")

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(gz-transport13 REQUIRED)
find_package(ros_gz_interfaces REQUIRED) 
find_package(gz-msgs10 REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GLFW REQUIRED glfw3)
find_package(OpenGL REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(OpenCV REQUIRED)
# первый нод
add_executable(cube_controller src/box_control.cpp)
ament_target_dependencies(cube_controller rclcpp geometry_msgs)

# второй нод
add_executable(gui_panel
 src/statistica.cpp
 ImGui/imgui.cpp
 ImGui/imgui_draw.cpp
 ImGui/imgui_tables.cpp
 ImGui/imgui_widgets.cpp
 ImGui/imgui_impl_glfw.cpp
 ImGui/imgui_impl_opengl3.cpp
)
set_target_properties(gui_panel PROPERTIES COMPILE_FLAGS "-O0 -g0")
# третий нод
add_executable(vision_processor
 src/raspoznovanie.cpp
)

target_include_directories(gui_panel PRIVATE 
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${OpenGL_INCLUDE_DIRS}
  ${GLFW_INCLUDE_DIRS}
  ${OpenCV_INCLUDE_DIRS}
)
target_link_libraries(gui_panel 
  ${GLFW_LIBRARIES}
  OpenGL::GL      
  ${OpenCV_LIBRARIES}
  cv_bridge::cv_bridge
)
ament_target_dependencies(gui_panel 
  rclcpp 
  geometry_msgs 
  std_msgs
  ros_gz_interfaces
  sensor_msgs
  cv_bridge  
  OpenCV 
)

ament_target_dependencies(vision_processor 
  rclcpp 
  sensor_msgs 
  std_msgs 
  cv_bridge 
  OpenCV
  geometry_msgs 
)


install(TARGETS
  cube_controller
  gui_panel
  vision_processor
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
