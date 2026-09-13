#pragma once
#include <esp_camera.h>

// The camera owns the memory. Return it once, including publication failures.
class CameraFrameLease {
 public:
  explicit CameraFrameLease(camera_fb_t* frame) : frame_(frame) {}
  ~CameraFrameLease() { if (frame_) esp_camera_fb_return(frame_); }
  CameraFrameLease(const CameraFrameLease&) = delete;
  CameraFrameLease& operator=(const CameraFrameLease&) = delete;

 private:
  camera_fb_t* frame_;
};
