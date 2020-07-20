# A bridge between Homematic and MQTT
This program receives events from a [Homematic](https://www.eq-3.de/produkte/homematic.html) CCU and publishes contained data on [MQTT](https://mqtt.org/). It also subscribes to commands from MQTT and relays them to the CCU.

Currently supported devices:
* [HmIP-BROLL](https://www.homematic-ip.com/en/products/detail/homematic-ip-shutter-actuator-for-brand-switches.html): Shutter actuator for brand switches
* [HmIP-SRH](https://www.homematic-ip.com/en/products/detail/homematic-ip-window-handle-sensor.html): Window handle sensor
* [HmIP-SWSD](https://www.homematic-ip.com/en/products/detail/homematic-ip-smoke-alarm-with-q-lable.html): Smoke alarm with Q label

[Home Assistant](https://www.home-assistant.io/), when configured for [MQTT discovery](https://www.home-assistant.io/docs/mqtt/discovery/), can auto-detect sensors and [device triggers](https://www.home-assistant.io/integrations/device_trigger.mqtt/) published by this program.

## Dependencies
* Python 3.7+
* [hbmqtt](https://pypi.org/project/hbmqtt/)
* [pyhomematic](https://pypi.org/project/pyhomematic/)

## How to use
```sh
hm-mqtt-bridge.py --broker mqtt://broker.local --listen 0.0.0.0 xmlrpc://ccu.local:2010
```
Homematic's XML-RPC mechanism requires listening sockets on both ends. By default, a random port number gets allocated by pyhomematic. If you need a fixed port number, specify it with `--listen <ip>:<port>`.
