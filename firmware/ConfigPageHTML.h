#ifndef CONFIG_PAGE_HTML_H
#define CONFIG_PAGE_HTML_H

const char CONFIG_PAGE_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ESP32 Setup</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 20px;
      background: #f0f0f0;
    }
    .container {
      max-width: 500px;
      margin: 0 auto;
      background: white;
      padding: 20px;
      border-radius: 8px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.1);
    }
    h1 {
      color: #333;
      text-align: center;
    }
    label {
      display: block;
      margin-top: 15px;
      font-weight: bold;
      color: #555;
    }
    input, select {
      width: 100%;
      padding: 10px;
      margin-top: 5px;
      border: 1px solid #ddd;
      border-radius: 4px;
      box-sizing: border-box;
    }
    button {
      width: 100%;
      padding: 12px;
      margin-top: 20px;
      background: #4CAF50;
      color: white;
      border: none;
      border-radius: 4px;
      cursor: pointer;
      font-size: 16px;
    }
    button:hover {
      background: #45a049;
    }
    .section {
      margin-top: 30px;
      padding-top: 20px;
      border-top: 2px solid #eee;
    }
    .status {
      text-align: center;
      padding: 10px;
      margin-top: 10px;
      border-radius: 4px;
    }
    .success {
      background: #d4edda;
      color: #155724;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>ESP32 Configuration</h1>

    <form action="/save" method="POST">
      <h2>WiFi Settings</h2>
      <label for="ssid">WiFi SSID:</label>
      <input type="text" id="ssid" name="ssid" required>

      <label for="password">WiFi Password:</label>
      <div style="display:flex;gap:8px;align-items:center;">
        <input type="password" id="password" name="password" required style="flex:1;">
        <button type="button" onclick="toggleWifiPass()" style="width:auto;margin:0;padding:10px;">Show</button>
      </div>
      <script>
        function toggleWifiPass() {
          var p = document.getElementById('password');
          var b = event.target;
          if (p.type === 'password') { p.type = 'text'; b.textContent = 'Hide'; }
          else { p.type = 'password'; b.textContent = 'Show'; }
        }
      </script>

      <div class="section">
        <h2>MQTT Settings</h2>
        <label for="mqtt_server">MQTT Server:</label>
        <input type="text" id="mqtt_server" name="mqtt_server" value="mqtt.deskbuddy.ai">

        <label for="mqtt_port">MQTT Port:</label>
        <input type="number" id="mqtt_port" name="mqtt_port" value="">

        <label for="mqtt_user">MQTT Username:</label>
        <input type="text" id="mqtt_user" name="mqtt_user" placeholder="device_id">

        <label for="mqtt_pass">MQTT Password:</label>
        <div style="display:flex;gap:8px;align-items:center;">
          <input type="password" id="mqtt_pass" name="mqtt_pass" style="flex:1;">
          <button type="button" onclick="togglePass()" style="width:auto;margin:0;padding:10px;">Show</button>
        </div>
        <script>
          function togglePass() {
            var p = document.getElementById('mqtt_pass');
            var b = event.target;
            if (p.type === 'password') { p.type = 'text'; b.textContent = 'Hide'; }
            else { p.type = 'password'; b.textContent = 'Show'; }
          }
        </script>

        <label for="mqtt_client_id">MQTT Client ID:</label>
        <input type="text" id="mqtt_client_id" name="mqtt_client_id" placeholder="ESP32Client">
      </div>

      <button type="submit">Save & Restart</button>
    </form>
  </div>
</body>
</html>
)rawliteral";

#endif // CONFIG_PAGE_HTML_H
