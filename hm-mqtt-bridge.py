#!/usr/bin/env python3
#
# Copyright 2020 Andreas Oberritter
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

import argparse
import asyncio
import json
import logging
import ssl
import sys
from functools import partial
from typing import Optional, Union
from urllib.parse import urlparse

from asyncio_mqtt import Client, MqttError, Will
from pyhomematic import HMConnection
from pyhomematic.devicetypes.actors import GenericBlind, GenericSwitch

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())


HM_COVER_CHANNEL_MAP = {4: 3}

MQTT_PREFIX = "Homematic"
HM_INTERFACE_ID = "mqttbridge"
HM_REMOTE = "default"

BINARY_SENSOR_TYPES = {
    "MAINTENANCE",
}

COVER_TYPES = {"SHUTTER_VIRTUAL_RECEIVER"}

DEVICE_TRIGGER_TYPES = {"KEY_TRANSCEIVER"}

SENSOR_TYPES = {
    "ALARM_COND_SWITCH_TRANSMITTER",
    "BLIND_WEEK_PROFILE",
    "COND_SWITCH_TRANSMITTER",
    "ENERGIE_METER_TRANSMITTER",
    "ROTARY_HANDLE_TRANSCEIVER",
    "SHUTTER_TRANSMITTER",
    "SMOKE_DETECTOR",
    "SWITCH_TRANSMITTER",
    "SWITCH_WEEK_PROFILE",
}

SWITCH_TYPES = {
    "SWITCH_VIRTUAL_RECEIVER",
}

SENSOR_UNITS = {
    "SHUTTER_TRANSMITTER": "%",
}

MAINTENANCE_FLAGS = {
    flag.lower()
    for flag in (
        "ACTUAL_TEMPERATURE_STATUS",
        "CONFIG_PENDING",
        "DUTY_CYCLE",
        "ERROR_CODE",
        "ERROR_OVERHEAT",
        "LOW_BAT",
        "OPERATING_VOLTAGE_STATUS",
        "SABOTAGE",
        "TIME_OF_OPERATION_STATUS",
        "UNREACH",
    )
}

ROTARY_HANDLE_VALUES = {
    0: "closed",
    1: "tilted",
    2: "open",
}
SMOKE_DETECTOR_VALUES = {
    0: "off",
    1: "primary",
    2: "intrusion",
    3: "secondary",
}


class HomematicMqttBridge:
    def __init__(self):
        self._loop = asyncio.get_running_loop()
        self._ha_devices = {}
        self._ha_attributes = {}

    def _subscribe(self, mqtt, topic: str) -> None:
        asyncio.run_coroutine_threadsafe(mqtt.subscribe(topic), self._loop)

    def _publish(self, mqtt, topic: str, message: Union[bool, bytes, dict, float, int, str], retain: Optional[bool] = True) -> None:
        if isinstance(message, dict):
            message = json.dumps(message)
        elif isinstance(message, bool):
            message = [b"OFF", b"ON"][message]
        elif isinstance(message, (float, int)):
            message = str(message)

        if isinstance(message, str):
            message = message.encode("utf-8")

        assert isinstance(message, bytes)

        asyncio.run_coroutine_threadsafe(
            mqtt.publish(topic, message, qos=2, retain=retain), self._loop
        )

    def _publish_discovery(self, mqtt, component: str, node_id: str, object_id: str, config: dict, retain: Optional[bool] = True) -> None:
        discovery_prefix = "homeassistant"
        topic = f"{discovery_prefix}/{component}/{node_id}/{object_id}/config"
        self._publish(mqtt, topic, config, retain=retain)

    def _publish_availability(self, mqtt, address: str, unreach: bool):
        if unreach:
            value = "offline"
        else:
            value = "online"

        base_topic = f"{MQTT_PREFIX}/{address}"
        self._publish(mqtt, "%s/availability" % base_topic, value)

    def _check_interface_id(self, interface_id: str) -> bool:
        return interface_id == HM_INTERFACE_ID + "-" + HM_REMOTE

    def _event_callback(self, mqtt, interface_id, address, value_key, value):
        logger.debug(f"event_callback({interface_id}, {address}, {value_key}, {value})")

        if not self._check_interface_id(interface_id):
            logger.error("Invalid interface ID: %s", interface_id)
            return

        attrs = self._ha_attributes.get(address)
        if not attrs:
            logger.error("Invalid address: %s", address)
            return

        parent, index = address.split(":", 1)
        base_topic = f"{MQTT_PREFIX}/{parent}/{index}"

        key = value_key
        lc_key = key.lower()
        old_value = attrs.get(lc_key)
        if old_value != value:
            attrs[lc_key] = value
            self._publish(mqtt, "%s/attributes" % base_topic, attrs)

        chan_type = attrs["type"]
        if chan_type == "MAINTENANCE":
            if key == "UNREACH":
                self._publish_availability(mqtt, parent, value)

            if lc_key in MAINTENANCE_FLAGS:
                state = sum(attrs[flag] for flag in MAINTENANCE_FLAGS if flag in attrs)
                self._publish(mqtt, "%s/state" % base_topic, bool(state))

        # HmIP-BROLL(1,2)
        elif chan_type == "KEY_TRANSCEIVER" and key in ("PRESS_SHORT", "PRESS_LONG"):
            assert isinstance(value, bool)
            self._publish(mqtt, f"{base_topic}/{key}", value, retain=False)
        # HmIP-BROLL(3)
        elif chan_type == "SHUTTER_TRANSMITTER" and key == "LEVEL":
            self._publish(mqtt, "%s/state" % base_topic, int(round(value * 100)))
        # HmIP-BROLL(4,5,6)
        elif chan_type == "SHUTTER_VIRTUAL_RECEIVER" and key == "LEVEL":
            self._publish(mqtt, "%s/state" % base_topic, int(round(value * 100)))
        # HmIP-BROLL(7), HmIP-BSM(9)
        elif chan_type.endswith("_WEEK_PROFILE") and key == "WEEK_PROGRAM_CHANNEL_LOCKS":
            self._publish(mqtt, "%s/state" % base_topic, value)
        # HmIP-SRH(1)
        elif chan_type == "ROTARY_HANDLE_TRANSCEIVER" and key == "STATE":
            text = ROTARY_HANDLE_VALUES.get(value, "unknown")
            self._publish(mqtt, "%s/state" % base_topic, text)
        # HmIP-SWSD(1)
        elif chan_type == "SMOKE_DETECTOR" and key == "SMOKE_DETECTOR_ALARM_STATUS":
            text = SMOKE_DETECTOR_VALUES.get(value, "unknown")
            self._publish(mqtt, "%s/state" % base_topic, text)
        # HmIP-BSM(4,5,6)
        elif chan_type == "SWITCH_VIRTUAL_RECEIVER" and key == "STATE":
            self._publish(mqtt, "%s/state" % base_topic, value)

    def _new_devices(self, mqtt, devices) -> None:
        logger.debug("new_devices()")
        for dev in devices:
            address = dev.get("ADDRESS")
            devtype = dev.get("TYPE")
            if address and devtype:
                self._ha_attributes[address] = {
                    k.lower(): v for k, v in dev.items() if v not in ("", [])
                }

                index = dev.get("INDEX")
                parent = dev.get("PARENT")
                parent_type = dev.get("PARENT_TYPE")

                if not parent:
                    self._ha_devices[address] = {
                        "name": f"{devtype}_{address}",
                        "identifiers": [address],
                        "manufacturer": "eQ-3",
                        "model": devtype,
                    }

                    firmware = dev.get("FIRMWARE")
                    if firmware:
                        self._ha_devices[address]["sw_version"] = firmware

                    logger.debug("New parent: %s", self._ha_devices[address])

                elif parent in self._ha_devices and index is not None:
                    node_id = f"{parent_type}_{parent}"
                    object_id = f"{index}-{devtype}"
                    parent_topic = f"{MQTT_PREFIX}/{parent}"
                    base_topic = f"{parent_topic}/{index}"
                    config = {
                        "device": self._ha_devices[parent],
                    }

                    if devtype in BINARY_SENSOR_TYPES:
                        # https://www.home-assistant.io/integrations/binary_sensor.mqtt/
                        config["availability_topic"] = "%s/availability" % parent_topic
                        config["json_attributes_topic"] = "%s/attributes" % base_topic
                        config["name"] = f"{parent_type} {devtype} {address}"
                        config["state_topic"] = "%s/state" % base_topic
                        config["unique_id"] = "Homematic-%s" % address
                        self._publish_discovery(mqtt, "binary_sensor", node_id, object_id, config)

                    elif devtype in SENSOR_TYPES:
                        # https://www.home-assistant.io/integrations/sensor.mqtt/
                        config["availability_topic"] = "%s/availability" % parent_topic
                        config["json_attributes_topic"] = "%s/attributes" % base_topic
                        config["name"] = f"{parent_type} {devtype} {address}"
                        config["state_topic"] = "%s/state" % base_topic
                        config["unique_id"] = "Homematic-%s" % address
                        unit_of_measurement = SENSOR_UNITS.get(devtype)
                        if unit_of_measurement:
                            config["unit_of_measurement"] = unit_of_measurement
                        self._publish_discovery(mqtt, "sensor", node_id, object_id, config)

                    elif devtype in DEVICE_TRIGGER_TYPES:
                        # https://www.home-assistant.io/integrations/device_trigger.mqtt/
                        component = "device_automation"
                        config["automation_type"] = "trigger"
                        config["subtype"] = "button_%s" % index
                        config["topic"] = "%s/PRESS_SHORT" % base_topic
                        config["type"] = "button_short_press"
                        self._publish_discovery(mqtt, component, node_id, object_id + "-short", config)
                        config["topic"] = "%s/PRESS_LONG" % base_topic
                        config["type"] = "button_long_press"
                        self._publish_discovery(mqtt, component, node_id, object_id + "-long", config)

                    elif devtype in COVER_TYPES:
                        # https://www.home-assistant.io/integrations/cover.mqtt/
                        config["availability_topic"] = "%s/availability" % parent_topic
                        config["command_topic"] = "%s/action" % base_topic
                        self._subscribe(mqtt, config["command_topic"])
                        config["device_class"] = "shutter"
                        config["json_attributes_topic"] = "%s/attributes" % base_topic
                        config["name"] = f"{parent_type} {devtype} {address}"
                        config["payload_close"] = "move_down"
                        config["payload_open"] = "move_up"
                        config["payload_stop"] = "stop"
                        config["position_closed"] = 0
                        config["position_open"] = 100

                        new_index = HM_COVER_CHANNEL_MAP.get(index)
                        if new_index is not None:
                            map_topic = f"{MQTT_PREFIX}/{parent}/%s" % new_index
                            config["position_topic"] = "%s/state" % map_topic
                        else:
                            config["position_topic"] = "%s/state" % base_topic

                        config["set_position_template"] = "{{ position / 100 }}"
                        config["set_position_topic"] = "%s/set_level" % base_topic
                        self._subscribe(mqtt, config["set_position_topic"])
                        config["unique_id"] = "Homematic-%s" % address
                        self._publish_discovery(mqtt, "cover", node_id, object_id, config)

                    elif devtype in SWITCH_TYPES:
                        # https://www.home-assistant.io/integrations/switch.mqtt/
                        config["availability_topic"] = "%s/availability" % parent_topic
                        config["command_topic"] = "%s/action" % base_topic
                        self._subscribe(mqtt, config["command_topic"])
                        config["json_attributes_topic"] = "%s/attributes" % base_topic
                        config["name"] = f"{parent_type} {devtype} {address}"
                        config["payload_off"] = "off"
                        config["payload_on"] = "on"
                        config["state_off"] = "OFF"
                        config["state_on"] = "ON"
                        config["state_topic"] = "%s/state" % base_topic
                        config["unique_id"] = "Homematic-%s" % address
                        self._publish_discovery(mqtt, "switch", node_id, object_id, config)

                    else:
                        logger.warning("Unhandled channel: %s", devtype)

                else:
                    logger.error("Parent not found!")

    def _system_callback(self, mqtt, src, *args):
        if src == "newDevices" and len(args) >= 2:
            if self._check_interface_id(args[0]):
                self._new_devices(mqtt, args[1])

    async def _process_packet(self, message, homematic):
        try:
            prefix, address, channel, name = message.topic.split("/")
        except ValueError:
            logger.error("Invalid topic: %s", message.topic)
            return

        if prefix != MQTT_PREFIX:
            logger.error("Invalid prefix: %s", prefix)
            return

        try:
            channel = int(channel)
        except ValueError:
            logger.error("Invalid channel: %s", channel)
            return

        try:
            data = message.payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.error("Invalid payload: %s", message.payload)
            return

        hmdevice = homematic.devices[HM_REMOTE].get(address)
        if not hmdevice:
            logger.error("Unable to find Homematic device %s", address)
            return

        hmchannel = hmdevice.CHANNELS.get(channel)
        if not hmchannel:
            logger.error("Invalid channel: %s", channel)
            return

        if isinstance(hmdevice, GenericBlind) and hmchannel.TYPE in COVER_TYPES:
            if name == "action":
                if data not in ("move_up", "move_down", "stop"):
                    logger.error("Invalid action: %s", data)
                    return

                logger.debug("%s:%s: %s()", address, channel, data)
                getattr(hmdevice, data)(channel)

            elif name == "set_level":
                try:
                    level = float(data)
                except ValueError:
                    logger.error("Invalid level: %s", data)
                    return

                if not 0 <= level <= 1:
                    logger.error("Invalid level: %s", level)
                    return

                logger.debug("%s:%d: set_level(%s)", address, channel, level)
                hmdevice.set_level(level, channel)

        elif isinstance(hmdevice, GenericSwitch) and hmchannel.TYPE in SWITCH_TYPES:
            if name == "action":
                if data not in ("on", "off"):
                    logger.error("Invalid action: %s", data)
                    return

                logger.debug("%s:%s: %s()", address, channel, data)
                getattr(hmdevice, data)(channel)

    def _xmlrpc_listen_url(self, url):
        if "://" not in url:
            url = f"//{url}"
        p = urlparse(url, scheme="xmlrpc")
        if p.scheme == "xmlrpc" and p.hostname:
            return p
        raise ValueError

    def _xmlrpc_connect_url(self, url):
        p = self._xmlrpc_listen_url(url)
        if p.port:
            return p
        raise ValueError

    async def run(self, broker: str, listen: str, connect: str) -> None:
        p = urlparse(broker, scheme="mqtt")
        if p.scheme not in ("mqtt", "mqtts") or not p.hostname:
            raise ValueError

        tls_context = None
        if p.scheme == "mqtts":
            tls_context = ssl.create_default_context()

        will = None
        async with Client(
            p.hostname,
            port=p.port or p.scheme == "mqtt" and 1883 or 8883,
            username=p.username,
            password=p.password,
            logger=logger,
            tls_context=tls_context,
            will=will,
        ) as mqtt:
            xmlrpc_local = self._xmlrpc_listen_url(listen)
            xmlrpc_remote = self._xmlrpc_connect_url(connect)
            homematic = HMConnection(
                interface_id=HM_INTERFACE_ID,
                local=xmlrpc_local.hostname,
                localport=xmlrpc_local.port or 0,
                remotes={
                    HM_REMOTE: {
                        "ip": xmlrpc_remote.hostname,
                        "port": xmlrpc_remote.port,
                        "path": xmlrpc_remote.path or "",
                        "username": xmlrpc_remote.username or "Admin",
                        "password": xmlrpc_remote.password or "",
                    }
                },
                eventcallback=partial(self._event_callback, mqtt),
                systemcallback=partial(self._system_callback, mqtt),
            )

            try:
                homematic.start()
            except AttributeError:
                sys.exit(1)

            async with mqtt.unfiltered_messages() as messages:
                async for message in messages:
                    await self._process_packet(message, homematic)

            homematic.stop()


async def main(cfg: dict) -> None:
    if cfg["debug"]:
        logger.setLevel(logging.DEBUG)

    await HomematicMqttBridge().run(cfg["broker"], cfg["listen"], cfg["connect"])


def options() -> dict:
    cfg = {
        "config": "/var/lib/hm-mqtt-bridge/config.json",
        "broker": "mqtt://localhost",
        "listen": "xmlrpc://0.0.0.0",
    }

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        help=f"Location of config file (default: {cfg['config']})",
    )
    parser.add_argument(
        "--broker",
        help=f"MQTT broker (default: {cfg['broker']})",
    )
    parser.add_argument(
        "--listen",
        help=f"Where to listen for connections from CCU (default: {cfg['listen']})",
    )
    parser.add_argument(
        "--connect",
        help="XML-RPC server of CCU, e.g. xmlrpc://ccu.local:2010",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable logging of debug messages",
    )

    args = parser.parse_args()
    filename = args.config or cfg["config"]

    try:
        with open(filename, "r") as f:
            cfg.update(json.load(f))
    except OSError as exc:
        if args.config or not isinstance(exc, FileNotFoundError):
            logger.error("Failed to open configuration file: %s", exc)
            sys.exit(1)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse configuration file: %s", exc)
        sys.exit(1)

    for key, value in vars(args).items():
        if value is not None:
            cfg[key] = value

    return cfg


try:
    asyncio.run(main(options()))
except MqttError as exc:
    logger.critical("MQTT error: %s", exc)
    sys.exit(1)
except KeyboardInterrupt:
    pass
