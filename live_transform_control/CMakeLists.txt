cmake_minimum_required(VERSION 3.16)
project(LiveTransformControl LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC OFF)
set(CMAKE_AUTORCC OFF)

find_package(PkgConfig REQUIRED)
pkg_check_modules(GZ_SIM_GUI REQUIRED IMPORTED_TARGET gz-sim8-gui)
pkg_check_modules(GZ_RENDERING REQUIRED IMPORTED_TARGET gz-rendering8)
pkg_check_modules(GZ_UTILS REQUIRED IMPORTED_TARGET gz-utils2)
pkg_check_modules(GZ_PLUGIN REQUIRED IMPORTED_TARGET gz-plugin2)
find_package(Qt5 REQUIRED COMPONENTS Core Quick)

qt5_wrap_cpp(PLUGIN_MOC LiveTransformControl.hh)
qt5_add_resources(PLUGIN_RCC LiveTransformControl.qrc)

add_library(LiveTransformControl SHARED
  LiveTransformControl.cc
  ${PLUGIN_MOC}
  ${PLUGIN_RCC}
)
target_link_libraries(LiveTransformControl PRIVATE
  PkgConfig::GZ_SIM_GUI
  PkgConfig::GZ_RENDERING
  PkgConfig::GZ_UTILS
  PkgConfig::GZ_PLUGIN
  Qt5::Core
  Qt5::Quick
)
set_target_properties(LiveTransformControl PROPERTIES
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}"
)
