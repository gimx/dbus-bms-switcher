#!/bin/bash

# Define paths and directories
SCRIPT_DIR=$(cd $(dirname $0) && pwd)
DATA_DIR="/data/dbus-bms-switcher"
SERVICE_DIR="/service/dbus-bms-switcher"
LOG_DIR="/var/log/dbus-bms-switcher"
QML_DIR="/opt/victronenergy/gui/qml"

# Parse input arguments
mode=-1
if [ "$1" == "INSTALL" ] || [ "$1" == "install" ]; then
    mode=0
elif [ "$1" == "UNINSTALL" ] || [ "$1" == "uninstall" ]; then
    mode=1
else
    echo "Available actions:"
    echo "INSTALL   -- Install dbus-bms-switcher service"
    echo "UNINSTALL -- Uninstall dbus-bms-switcher service" 
    exit -1
fi

# --- INSTALL ACTION ---
if [ $mode == 0 ]; then
    echo "Do install"

    # 1. Copy files to /data/dbus-bms-switcher
    echo "Deploying application files..."
    mkdir -p $DATA_DIR
    cp -rf $SCRIPT_DIR/* $DATA_DIR/
    chmod +x $DATA_DIR/dbus-bms-switcher.py
    chmod +x $DATA_DIR/service/run

    # 2. Setup logging directory and log runner
    mkdir -p $LOG_DIR
    mkdir -p $DATA_DIR/service/log
    cat << 'EOF' > $DATA_DIR/service/log/run
#!/bin/sh
exec multilog t s250000 n4 /var/log/dbus-bms-switcher
EOF
    chmod +x $DATA_DIR/service/log/run

    # 3. Handle QML GUI Integration
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

    # 4. Link service for daemontools
    if [ ! -d "$SERVICE_DIR" ] && [ ! -L "$SERVICE_DIR" ]; then
        echo "Linking service to /service..."
        ln -s $DATA_DIR/service $SERVICE_DIR
    fi

    # 5. Make service persistent across reboots via /data/rc.local
    if [ -f /data/rc.local ] && grep -qxF "ln -s /data/dbus-bms-switcher/service /service/dbus-bms-switcher" /data/rc.local; then
        echo "Service persistence already configured in /data/rc.local"
    else
        echo "Adding persistence entry to /data/rc.local..."
        echo "ln -s /data/dbus-bms-switcher/service /service/dbus-bms-switcher" >> /data/rc.local
        chmod +x /data/rc.local
    fi

    echo "Installation complete."

# --- UNINSTALL ACTION ---
else
    echo "Do uninstall"

    # 1. Stop and remove daemontools service
    if [ -d "$SERVICE_DIR" ] || [ -L "$SERVICE_DIR" ]; then
        echo "Stopping and removing service..."
        svc -d $SERVICE_DIR
        svc -x $SERVICE_DIR
        rm -f $SERVICE_DIR
    fi

    # 2. Clean persistence entry from /data/rc.local
    if [ -f /data/rc.local ]; then
        echo "Removing entry from /data/rc.local..."
        sed -i '\|ln -s /data/dbus-bms-switcher/service /service/dbus-bms-switcher|d' /data/rc.local
    fi

    # 3. Clean QML modifications
    if [ -f "$QML_DIR/PageBmsSwitcher.qml" ]; then
        echo "Removing QML GUI files..."
        rm -f $QML_DIR/PageBmsSwitcher.qml
        svc -t /service/gui
    fi

    # 4. Clean application data
    echo "Removing /data/dbus-bms-switcher..."
    rm -rf $DATA_DIR

    echo "Uninstallation complete."
fi
