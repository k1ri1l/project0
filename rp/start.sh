#!/bin/bash

echo "1. Запрашиваю права sudo и чищу старые процессы..."
sudo -v
sudo pkill -9 -f openocd 2>/dev/null || true
sudo pkill -9 -f socat 2>/dev/null || true
sudo rm -f /dev/ttySTM32 /dev/ttyACM1

echo "2. Запускаю OpenOCD RTT..."
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
  -c "init" -c "reset run" -c "sleep 500" \
  -c "rtt setup 0x2000080c 0x400 \"SEGGER RTT\"" \
  -c "rtt start" -c "rtt server start 9090 0" >/tmp/openocd.log 2>&1 &
sleep 2

echo "3. Создаю виртуальный порт /dev/ttySTM32..."
sudo socat pty,link=/dev/ttySTM32,raw,echo=0,mode=666 tcp:127.0.0.1:9090,forever,retry=1 >/tmp/socat.log 2>&1 &
sleep 1

echo "4. Запускаю main.py..."
cd ~/meshrp
source venv/bin/activate
exec python3 main.py
