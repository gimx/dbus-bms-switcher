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

# 5b. gui-v2 UI plugin (Settings -> Integrations -> UI Plugins -> BmsSwitcher)
#     Works on the local display and in the remote console / VRM; no Victron files are modified.
PLUGIN_JSON="$SCRIPT_DIR/gui-v2/BmsSwitcher/BmsSwitcher.json"
APP_AVAILABLE_DIR="/data/apps/available/BmsSwitcher"
APP_ENABLED_LINK="/data/apps/enabled/BmsSwitcher"
# NOTE: dbus-flashmq (remote console / VRM) requires <app-dir>/gui-v2/<app-dir>.json, so the app dir must be "BmsSwitcher".
if [ -L "/data/apps/enabled/dbus-bms-switcher" ]; then
    echo "Removing plugin from earlier install with wrong app directory name..."
    rm -f "/data/apps/enabled/dbus-bms-switcher"
    rm -rf "/data/apps/available/dbus-bms-switcher"
fi
if [ -f "$PLUGIN_JSON" ]; then
    mkdir -p "$APP_AVAILABLE_DIR/gui-v2" /data/apps/enabled
    if ! cmp -s "$PLUGIN_JSON" "$APP_AVAILABLE_DIR/gui-v2/BmsSwitcher.json"; then
        echo "Installing gui-v2 UI plugin..."
        cp -f "$PLUGIN_JSON" "$APP_AVAILABLE_DIR/gui-v2/BmsSwitcher.json"
        pluginChanged=1
    else
        echo "gui-v2 UI plugin already up to date"
    fi
    if [ ! -e "$APP_ENABLED_LINK" ]; then
        ln -s "$APP_AVAILABLE_DIR" "$APP_ENABLED_LINK"
        pluginChanged=1
    fi
    if [ "$pluginChanged" = "1" ]; then
        if [ -d "/service/gui" ]; then guiService="/service/gui"; else guiService="/service/start-gui"; fi
        if [ -d "$guiService" ]; then
            echo "Restarting GUI to load the plugin..."
            svc -t "$guiService"
        fi
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
