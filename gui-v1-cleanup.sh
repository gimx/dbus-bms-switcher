#!/bin/bash
# Sourced by install.sh and uninstall.sh.
#
# Earlier versions copied qml/PageSettings.qml and qml/PageBmsSwitcher.qml into the GUI v1 qml folder.
# That PageSettings.qml replaced Victron's settings menu, and PageBmsSwitcher.qml imports QtQuick 1.1,
# which is not available on Venus OS v3.x, so GUI v1 failed to load. This removes both again.

GUI_V1_QML_DIR="/opt/victronenergy/gui/qml"
GUI_V1_OVERLAY_QML_DIR="/data/apps/overlay-fs/data/gui/upper/qml"

remove_gui_v1_pages() {
    local changed=0

    if [ -d "$GUI_V1_OVERLAY_QML_DIR" ]; then
        # /opt/victronenergy/gui is an overlay: remove the files from its upper dir, so Victron's
        # PageSettings.qml shows through again (deleting via the mount would hide it too)
        if [ -f "$GUI_V1_OVERLAY_QML_DIR/PageBmsSwitcher.qml" ]; then
            rm -f "$GUI_V1_OVERLAY_QML_DIR/PageBmsSwitcher.qml"
            changed=1
        fi
        if [ -f "$GUI_V1_OVERLAY_QML_DIR/PageSettings.qml" ] \
            && grep -Fq "PageBmsSwitcher" "$GUI_V1_OVERLAY_QML_DIR/PageSettings.qml"; then
            rm -f "$GUI_V1_OVERLAY_QML_DIR/PageSettings.qml"
            changed=1
        fi
        if [ $changed -eq 1 ]; then
            sync
            echo 2 > /proc/sys/vm/drop_caches
        fi
    fi

    if [ -f "$GUI_V1_QML_DIR/PageBmsSwitcher.qml" ]; then
        # copied directly onto the root filesystem
        rm -f "$GUI_V1_QML_DIR/PageBmsSwitcher.qml" 2>/dev/null && changed=1
    fi
    if [ -f "$GUI_V1_QML_DIR/PageSettings.qml" ] && grep -Fq "PageBmsSwitcher" "$GUI_V1_QML_DIR/PageSettings.qml"; then
        echo "WARNING: $GUI_V1_QML_DIR/PageSettings.qml was overwritten by an earlier install and cannot be"
        echo "         restored automatically, reinstall the Venus OS firmware to get Victron's version back."
    fi

    if [ $changed -eq 1 ]; then
        echo "Removed the GUI v1 pages of an earlier install"
        if [ -d /service/gui ]; then
            svc -t /service/gui
        fi
    fi
}
