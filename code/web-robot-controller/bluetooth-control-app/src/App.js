import React, { useState } from 'react';
import './App.css';

function App() {
  const [device, setDevice] = useState(null);
  const [characteristic, setCharacteristic] = useState(null);

  const requestBluetoothDevice = () => {
    navigator.bluetooth.requestDevice({
      // Example for using the generic UUID for Serial Port Profile
      acceptAllDevices: true,
      optionalServices: ['00001101-0000-1000-8000-00805f9b34fb']
    })
    .then(selectedDevice => {
      setDevice(selectedDevice);
      return selectedDevice.gatt.connect();
    })
    .then(server => {
      // Get your specific service and characteristic here
      // For example:
      // return server.getPrimaryService('your-service-id');
    })
    .then(service => {
      // Get characteristic
      // For example:
      // return service.getCharacteristic('your-characteristic-id');
    })
    .then(ch => {
      setCharacteristic(ch);
    })
    .catch(error => {
      console.log(error);
    });
  };

  const sendCommand = (commandString) => {
    if (characteristic) {
      const command = new TextEncoder().encode(commandString);
      characteristic.writeValue(command)
      .then(() => {
        console.log('Command sent');
      })
      .catch(error => {
        console.log(error);
      });
    } else {
      console.log('Characteristic not defined');
    }
  };

  return (
    <div className="App">
      <header className="App-header">
        <p>Bluetooth APP</p>
        <button onClick={requestBluetoothDevice}>Connect to Bluetooth Device</button>
        {/* Example button to send a command */}
        <button onClick={() => sendCommand('Your Command')}>Send Command</button>
      </header>
    </div>
  );
}

export default App;
