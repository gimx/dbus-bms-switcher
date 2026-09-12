#!/bin/bash

SCRIPT_DIR=$(cd $(dirname $0) && pwd)
DATA_DIR="/data/dbus-bms-switcher"
SERVICE_DIR="/service/dbus-bms-switcher"
LOG_DIR="/var/log/dbus-bms-switcher"
QML_DIR="/opt/victronenergy/gui/qml"

echo "Do install"

# 1. Stop service if already running during re-install
if [ -d "$SERVICE_DIR" ] || [ -L "$SERVICE_DIR" ]; then
    svc -d $SERVICE_DIR $SERVICE_DIR/log 2>/dev/null
fi

# 2. Create destination directories
mkdir -p $DATA_DIR
mkdir -p $LOG_DIR

# 3. Copy application files and existing service structure directly
echo "Deploying application and existing service files..."
cp -rf $SCRIPT_DIR/* $DATA_DIR/

# 4. Ensure existing run scripts and python executable have proper permissions
chmod +x $DATA_DIR/dbus-bms-switcher.py
if [ -f "$DATA_DIR/service/run" ]; then
    chmod +x $DATA_DIR/service/run
fi
if [ -f "$DATA_DIR/service/log/run" ]; then
    chmod +x $DATA_DIR/service/log/run
fi

# 5. Handle QML GUI Integration
if [ -f "$QML_DIR/PageSettings.qml" ]; then
    if grep -Fq "PageBmsSwitcher" $QML_DIR/PageSettings.qml; then
        echo "GUI menu already in PageSettings.qml"
    else
        echo "GUI menu not found, adding QML overlay..."
        if [ -d "$SCRIPT_DIR/qml" ]; then
            cp -rf $SCRIPT_DIR/qml/* $QML_DIR/
        fi
        svc -t /service/gui
    fi
fi

# 6. Link service directory to /service for daemontools
if [ ! -d "$SERVICE_DIR" ] && [ ! -L "$SERVICE_DIR" ]; then
    echo "Linking service to /service..."
    ln -s $DATA_DIR/service $SERVICE_DIR
else
    echo "Restarting active service..."
    svc -u $SERVICE_DIR
fi

# 7. Make service persistent across reboots via /data/rc.local
if [ -f /data/rc.local ] && grep -qxF "ln -s /data/dbus-bms-switcher/service /service/dbus-bms-switcher" /data/rc.local; then
    echo "Service persistence already configured in /data/rc.local"
else
    echo "Adding persistence entry to /data/rc.local..."
    echo "ln -s /data/dbus-bms-switcher/service /service/dbus-bms-switcher" >> /data/rc.local
    chmod +x /data/rc.local
fi

echo "Installation complete."
