# A bridge between Homematic and MQTT
This program receives events from a [Homematic](https://www.eq-3.de/produkte/homematic.html) CCU and publishes contained data on [MQTT](https://mqtt.org/). It also subscribes to commands from MQTT and relays them to the CCU.

Currently supported devices:
* [HmIP-BROLL](https://www.homematic-ip.com/en/products/detail/homematic-ip-shutter-actuator-for-brand-switches.html): Shutter actuator for brand switches
* [HmIP-SRH](https://www.homematic-ip.com/en/products/detail/homematic-ip-window-handle-sensor.html): Window handle sensor
* [HmIP-SWSD](https://www.homematic-ip.com/en/products/detail/homematic-ip-smoke-alarm-with-q-lable.html): Smoke alarm with Q label

[Home Assistant](https://www.home-assistant.io/), when configured for [MQTT discovery](https://www.home-assistant.io/docs/mqtt/discovery/), can auto-detect sensors and [device triggers](https://www.home-assistant.io/integrations/device_trigger.mqtt/) published by this program.

## Dependencies
* Python 3.7+
* [asyncio-mqtt](https://pypi.org/project/asyncio-mqtt/) >= 0.7.0
* [pyhomematic](https://pypi.org/project/pyhomematic/)

## How to use
```sh
hm-mqtt-bridge.py --broker mqtt://broker.local --listen 0.0.0.0 xmlrpc://ccu.local:2010
```
Homematic's XML-RPC mechanism requires listening sockets on both ends. By default, a random port number gets allocated by pyhomematic. If you need a fixed port number, specify it with `--listen <ip>:<port>`.

## Docker
There is also a rudimentary docker container available. It checks out the latest version of this repository and runs it in a debian container with python3 and all the necessary dependencies installed.
```sh
docker build --pull -t homematic-mqtt-bridge .
docker run -it homematic-mqtt-bridge /opt/homematic-mqtt-bridge/hm-mqtt-bridge.py ....
```

## How to get new devices supported
In order to support new devices, I need their names and the channels they use. To help with that you can run the hm-inventory.py script. Either directly:
```sh
python3.7 -m venv $someDirectory
source $someDirectory/bin/activate
hm-inventory.py --connect xmlrpc://$ccuIP:2010 > out.txt
```
or using the docker container:
```sh
docker build --pull -t homematic-mqtt-bridge .
docker run -it homematic-mqtt-bridge /opt/homematic-mqtt-bridge/hm-inventory.py --connect xmlrpc://$ccuIP:2010 > out.txt
```

It does not stop running, as it records incoming events from the devices. It helps if you can press some buttons on the devices in question. If you are done, stop the script (ctrl-c) and anonymize the data in the out.txt:

```sh
sed -i "s/'RF_ADDRESS': [0-9]\{7\}/'RF_ADDRESS': 0000000/g" out.txt
sed -i "s/'[A-Z0-9]\{12\}/'000000000000/g" out.txt
```

Post this file as a new issue with the device name you want supported.
