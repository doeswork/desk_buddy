#pragma once
#include <Arduino.h>        // for String, Serial
#include <ArduinoJson.h>    // for JSON

class ActionController {
public:
    // dispatch parses the JSON, picks the action and runs it
    static void dispatch(const String& message);
};
