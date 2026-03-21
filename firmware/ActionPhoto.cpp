// ActionPhoto.cpp

#include "camera_pins.h"
#include "ActionPhoto.h"
#include "BuddyWifi.h"
#include "BuddyMQTT.h"
#include "ActionBaseRotate.h"

#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include <cstring>

namespace {
  bool cameraInited = false;

  String readJsonString(const JsonVariantConst& v) {
    if (v.is<const char*>()) {
      const char* raw = v.as<const char*>();
      if (!raw) return String();
      if (strcmp(raw, "null") == 0) return String();
      return String(raw);
    }
    if (v.is<String>()) {
      String s = v.as<String>();
      if (s == "null") return String();
      return s;
    }
    if (v.is<long>())    return String(v.as<long>());
    if (v.is<int>())     return String(v.as<int>());
    if (v.is<unsigned>()) return String(v.as<unsigned>());
    if (v.is<float>())   return String(v.as<float>(), 6);
    if (v.is<double>())  return String(v.as<double>(), 6);
    return String();
  }

  void logMem(const char* tag) {
    size_t dram = heap_caps_get_free_size(MALLOC_CAP_8BIT);
    size_t ps   = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    Serial.printf("[%s] Free DRAM: %u  Free PSRAM: %u\n", tag, (unsigned)dram, (unsigned)ps);
  }

  bool tryInit(framesize_t fs, int jpeg_quality) {
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer   = LEDC_TIMER_0;
    config.pin_pwdn     = PWDN_GPIO_NUM;
    config.pin_reset    = RESET_GPIO_NUM;
    config.pin_xclk     = XCLK_GPIO_NUM;
    config.pin_sscb_sda = SIOD_GPIO_NUM;
    config.pin_sscb_scl = SIOC_GPIO_NUM;
    config.pin_d7       = Y9_GPIO_NUM;
    config.pin_d6       = Y8_GPIO_NUM;
    config.pin_d5       = Y7_GPIO_NUM;
    config.pin_d4       = Y6_GPIO_NUM;
    config.pin_d3       = Y5_GPIO_NUM;
    config.pin_d2       = Y4_GPIO_NUM;
    config.pin_d1       = Y3_GPIO_NUM;
    config.pin_d0       = Y2_GPIO_NUM;
    config.pin_vsync    = VSYNC_GPIO_NUM;
    config.pin_href     = HREF_GPIO_NUM;
    config.pin_pclk     = PCLK_GPIO_NUM;

    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size   = fs;
    config.jpeg_quality = jpeg_quality;     // 10..63 (lower = better quality, more RAM)
    config.fb_count     = 1;                // keep at 1 to reduce RAM
    config.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

    // Prefer PSRAM for frame buffers when available
    if (psramFound()) {
      config.fb_location = CAMERA_FB_IN_PSRAM;
    } else {
      config.fb_location = CAMERA_FB_IN_DRAM;
    }

    logMem("pre-init");
    esp_err_t err = esp_camera_init(&config);
    if (err == ESP_OK) {
      Serial.printf("Camera init OK at framesize=%d quality=%d (psram=%s)\n",
                    (int)fs, jpeg_quality, psramFound() ? "yes" : "no");
      return true;
    } else {
      Serial.printf("Camera init FAILED (err=0x%08x) at framesize=%d quality=%d\n",
                    (unsigned)err, (int)fs, jpeg_quality);
      return false;
    }
  }

  void deinitCameraIfNeeded() {
    if (cameraInited) {
      esp_camera_deinit();
      cameraInited = false;
      logMem("after-deinit");
    }
  }

  void initCamera() {
    if (cameraInited) return;

    // Try progressively smaller frame sizes to fit memory
    const framesize_t candidates[] = {
      FRAMESIZE_SVGA, // 800x600
      FRAMESIZE_VGA,  // 640x480
      FRAMESIZE_QVGA  // 320x240 (almost always fits)
    };
    // Start with a moderate quality to reduce buffer size
    const int jpeg_quality = 20;

    for (framesize_t fs : candidates) {
      if (tryInit(fs, jpeg_quality)) {
        cameraInited = true;
        return;
      }
      // If a try failed, ensure clean state before the next
      esp_camera_deinit(); // Only deinit on actual failure
      delay(50);
    }

    Serial.println("Camera init failed after all fallbacks");
  }

  // Optionally try to recover once if capture fails (FB malloc fragmentation)
  bool recoverCameraOnce() {
    deinitCameraIfNeeded();
    delay(50);
    initCamera();
    return cameraInited;
  }

}

void ActionPhoto::initializeCamera() {
  initCamera();
}

void ActionPhoto::run(const String &message, int useModel) {
  // Parse JSON payload
  DynamicJsonDocument doc(512);
  auto err = deserializeJson(doc, message);
  if (err) {
    Serial.print("JSON parse error: ");
    Serial.println(err.c_str());
    return;
  }

  String sender   = readJsonString(doc["sender"]);
  String actionId = readJsonString(doc["action_id"]);
  String action   = readJsonString(doc["action"]);
  JsonVariantConst phrase = doc["phrase"];
  JsonVariantConst useModelRaw = doc["use_model"];
  if (useModelRaw.isNull()) {
    useModelRaw = doc["useModel"];
  }
  String useModelJson;
  if (!useModelRaw.isNull()) {
    serializeJson(useModelRaw, useModelJson);
  }
  int magnetPosition = ActionBaseRotate::getOriginMagnet();

  Serial.printf("ActionPhoto triggered by %s, action_id=%s, action=%s\n",
                sender.c_str(), actionId.c_str(), action.c_str());

  Serial.println(action);
  Serial.println("this is the action ^");
  if (!(action == "photo" || action == "detect_color" || action == "detect_object" || action == "calibrate_depth")) {
    Serial.println("Not a photo action; skipping");
    return;
  }
  if (!BuddyWifi::isConnected()) {
    Serial.println("WiFi not connected; skipping camera");
    return;
  }

  // Ensure camera ready (with fallbacks)
  if (!cameraInited) {
    initCamera();
    if (!cameraInited) {
      Serial.println("Camera init failed");
      return;
    }
  }

  // Flush any cached frame buffer and capture fresh frame
  camera_fb_t* flush_fb = esp_camera_fb_get();
  if (flush_fb) {
    esp_camera_fb_return(flush_fb);
    delay(10); // Brief delay to ensure sensor updates
  }

  // Capture fresh frame (with one-shot recovery)
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("Camera capture failed (fb=null), attempting one recovery re-init...");
    if (!recoverCameraOnce()) {
      Serial.println("Recovery re-init failed");
      return;
    }
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed again");
      return;
    }
  }

  Serial.printf("Photo captured: %d bytes\n", fb->len);

  bool published = BuddyMQTT::publishStatusPhoto(actionId, sender, fb->buf, fb->len, phrase, magnetPosition, useModel, useModelJson);
  if (published) {
    Serial.printf("Photo payload published to status topic (%d bytes)\n", fb->len);
  } else {
    Serial.println("Photo publish failed");
  }

  // Always return FB to free memory ASAP
  esp_camera_fb_return(fb);
}
