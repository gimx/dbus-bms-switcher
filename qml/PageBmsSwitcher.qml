import QtQuick 1.1
import "com.victron.velib"

MbPage {
    id: root
    title: qsTr("BMS Profile Switcher")

    // Bind to the DBus path on your Python service
    VBusItem {
        id: switchTriggerItem
        bind: "com.victronenergy.bmsswitcher/Settings/BmsSwitcher/Trigger"
    }

    model: VisualItemModel {

        MbItemAction {
            description: qsTr("Switch BMS Profile (CAN / Serial)")
            show: true
            onClicked: {
                switchTriggerItem.setValue(1)
            }
        }
    }
}
