#!/bin/bash

SCRIPT_DIR=$(cd $(dirname $0) && pwd)
source "$SCRIPT_DIR/gui-v1-cleanup.sh"
DATA_DIR="/data/dbus-bms-switcher"
SERVICE_DIR="/service/dbus-bms-switcher"
APP_AVAILABLE_DIR="/data/apps/available/BmsSwitcher"
APP_ENABLED_LINK="/data/apps/enabled/BmsSwitcher"
RC_LINE="ln -s /data/dbus-bms-switcher/service /service/dbus-bms-switcher"

echo "Do uninstall"

# 1. Stop and unlink service
if [ -d "$SERVICE_DIR" ] || [ -L "$SERVICE_DIR" ]; then
    echo "Stopping service..."
    svc -d $SERVICE_DIR $SERVICE_DIR/log 2>/dev/null
    rm -f $SERVICE_DIR
fi

# 2. Remove persistence entry from /data/rc.local
if [ -f /data/rc.local ] && grep -qxF "$RC_LINE" /data/rc.local; then
    echo "Removing persistence entry from /data/rc.local..."
    grep -vxF "$RC_LINE" /data/rc.local > /data/rc.local.tmp && cat /data/rc.local.tmp > /data/rc.local
    rm -f /data/rc.local.tmp
fi

# 3. Remove gui-v2 UI plugin
if [ -e "$APP_ENABLED_LINK" ] || [ -L "$APP_ENABLED_LINK" ]; then
    echo "Removing gui-v2 UI plugin..."
    rm -f "$APP_ENABLED_LINK"
    rm -rf "$APP_AVAILABLE_DIR"
    if [ -d "/service/gui" ]; then guiService="/service/gui"; else guiService="/service/start-gui"; fi
    [ -d "$guiService" ] && svc -t "$guiService"
fi

# 4. Remove the GUI v1 pages of earlier installs
remove_gui_v1_pages

echo "Uninstall complete. Application files remain in $DATA_DIR (incl. config.ini); delete manually if no longer needed."
