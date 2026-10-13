# generated from ament/cmake/core/templates/nameConfig.cmake.in

# prevent multiple inclusion
if(_proect_manipulator_CONFIG_INCLUDED)
  # ensure to keep the found flag the same
  if(NOT DEFINED proect_manipulator_FOUND)
    # explicitly set it to FALSE, otherwise CMake will set it to TRUE
    set(proect_manipulator_FOUND FALSE)
  elseif(NOT proect_manipulator_FOUND)
    # use separate condition to avoid uninitialized variable warning
    set(proect_manipulator_FOUND FALSE)
  endif()
  return()
endif()
set(_proect_manipulator_CONFIG_INCLUDED TRUE)

# output package information
if(NOT proect_manipulator_FIND_QUIETLY)
  message(STATUS "Found proect_manipulator: 0.0.0 (${proect_manipulator_DIR})")
endif()

# warn when using a deprecated package
if(NOT "" STREQUAL "")
  set(_msg "Package 'proect_manipulator' is deprecated")
  # append custom deprecation text if available
  if(NOT "" STREQUAL "TRUE")
    set(_msg "${_msg} ()")
  endif()
  # optionally quiet the deprecation message
  if(NOT proect_manipulator_DEPRECATED_QUIET)
    message(DEPRECATION "${_msg}")
  endif()
endif()

# flag package as ament-based to distinguish it after being find_package()-ed
set(proect_manipulator_FOUND_AMENT_PACKAGE TRUE)

# include all config extra files
set(_extras "")
foreach(_extra ${_extras})
  include("${proect_manipulator_DIR}/${_extra}")
endforeach()
