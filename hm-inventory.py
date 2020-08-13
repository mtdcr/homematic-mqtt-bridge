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
import json
import logging
import sys
from pprint import pformat
from signal import SIG_DFL, SIGINT, SIGTERM, signal
from typing import Optional
from urllib.parse import ParseResult, urlparse

from pyhomematic import HMConnection

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.setLevel(logging.INFO)


class HomematicInventory:
    def _event_callback(
        self, interface_id: str, address: str, value_key: str, value: str
    ) -> None:
        logger.info("event: %s", pformat([address, value_key, value]))

    def _system_callback(self, src: str, *args) -> None:
        logger.info("system: %s: %s", src, pformat(args))

    def _xmlrpc_listen_url(self, url: str) -> Optional[ParseResult]:
        if "://" not in url:
            url = f"//{url}"
        p = urlparse(url, scheme="xmlrpc")
        if p.scheme != "xmlrpc":
            raise ValueError("Invalid scheme")
        if not p.hostname:
            raise ValueError("Missing hostname")
        return p

    def _xmlrpc_connect_url(self, url: str) -> Optional[ParseResult]:
        p = self._xmlrpc_listen_url(url)
        if not p.port:
            raise ValueError("Missing port number")
        return p

    def run(self, listen: str, connect: str) -> None:
        try:
            xmlrpc_local = self._xmlrpc_listen_url(listen)
            xmlrpc_remote = self._xmlrpc_connect_url(connect)
        except ValueError as exc:
            logger.error("Invalid XML-RPC URL: %s", exc)
            sys.exit(1)

        homematic = HMConnection(
            interface_id="inventory",
            local=xmlrpc_local.hostname,
            localport=xmlrpc_local.port or 0,
            remotes={
                "default": {
                    "ip": xmlrpc_remote.hostname,
                    "port": xmlrpc_remote.port,
                    "path": xmlrpc_remote.path or "",
                    "username": xmlrpc_remote.username or "Admin",
                    "password": xmlrpc_remote.password or "",
                }
            },
            eventcallback=self._event_callback,
            systemcallback=self._system_callback,
        )

        try:
            homematic.start()
        except AttributeError:
            sys.exit(1)

        def handler(signum, frame):
            signal(signum, SIG_DFL)
            homematic.stop()

        for signum in (SIGINT, SIGTERM):
            signal(signum, handler)

        homematic._server.join()


def options() -> dict:
    cfg = {
        "config": "/var/lib/hm-mqtt-bridge/config.json",
        "listen": "xmlrpc://0.0.0.0",
    }

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", help=f"Location of config file (default: {cfg['config']})"
    )
    parser.add_argument(
        "--listen",
        help=f"Where to listen for connections from CCU (default: {cfg['listen']})",
    )
    parser.add_argument(
        "--connect", help="XML-RPC server of CCU, e.g. xmlrpc://ccu.local:2010"
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

    if "connect" not in cfg:
        logger.error("Missing required parameter: connect")
        sys.exit(1)

    return cfg


cfg = options()
try:
    HomematicInventory().run(cfg["listen"], cfg["connect"])
except KeyboardInterrupt:
    pass
