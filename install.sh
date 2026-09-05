#!/bin/bash
mkdir -p /data/dbus-bms-switcher
cp -f dbus-bms-switcher.py /data/dbus-bms-switcher/
chmod +x /data/dbus-bms-switcher/dbus-bms-switcher.py

mkdir -p /service/dbus-bms-switcher
cp -f service/run /service/dbus-bms-switcher/
chmod +x /service/dbus-bms-switcher/run

echo "Installation complete. Service dbus-bms-switcher starting..."
