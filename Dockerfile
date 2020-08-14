#FROM arm32v5/debian:buster
FROM debian:buster

RUN apt-get update && apt-get -y install ca-certificates python3-pip git

RUN pip3 install pyhomematic asyncio-mqtt && cd /opt && git clone https://github.com/mtdcr/homematic-mqtt-bridge

