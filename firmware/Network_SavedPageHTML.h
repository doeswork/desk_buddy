#ifndef SAVED_PAGE_HTML_H
#define SAVED_PAGE_HTML_H

const char SAVED_PAGE_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Saved</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 20px;
      background: #f0f0f0;
      text-align: center;
    }
    .container {
      max-width: 500px;
      margin: 50px auto;
      background: white;
      padding: 40px;
      border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.1);
    }
    h1 {
      color: #4CAF50;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>Settings Saved!</h1>
    <p>The ESP32 will now restart and connect to your WiFi network.</p>
    <p>This configuration page will close automatically.</p>
  </div>
</body>
</html>
)rawliteral";

#endif // SAVED_PAGE_HTML_H
