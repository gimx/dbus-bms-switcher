/*
** dbus-bms-switcher - gui-v2 UI plugin settings page
** Shown under Settings -> Integrations -> UI Plugins -> BmsSwitcher.
**
** Written against the gui-v2 v1.3.x plugin API (Venus OS v3.8x), i.e. a Victron.VenusOS Page.
** Uses BackendConnection.serviceUidForType() so it works over D-Bus (local display)
** as well as MQTT (remote console / VRM). The latter requires /DeviceInstance = 0 on the service.
*/

import QtQuick
import Victron.VenusOS

Page {
	id: root

	title: "BMS Switcher"

	readonly property string serviceUid: BackendConnection.serviceUidForType("bmsswitcher")
	readonly property bool serviceOnline: activeBmsItem.valid
	readonly property bool busy: busyItem.valid && busyItem.value === 1
	readonly property bool dynamicMinSoc: minSocItem.valid && minSocItem.value < 0

	VeQuickItem { id: activeBmsItem; uid: root.serviceUid + "/ActiveBms" }
	VeQuickItem { id: busyItem; uid: root.serviceUid + "/Busy" }
	VeQuickItem { id: minSocItem; uid: root.serviceUid + "/Settings/BmsSwitcher/MinSoc" }
	VeQuickItem { id: maxSocItem; uid: root.serviceUid + "/Settings/BmsSwitcher/MaxSoc" }
	VeQuickItem { id: activeMinSocItem; uid: root.serviceUid + "/ActiveMinSoc" }
	VeQuickItem { id: triggerItem; uid: root.serviceUid + "/TriggerSwitch" }
	VeQuickItem { id: standbyAlarmItem; uid: root.serviceUid + "/StandbyAlarmAtDisconnect" }

	GradientListView {
		model: VisibleItemModel {

			ListText {
				text: "Service"
				secondaryText: "Not running"
				preferredVisible: !root.serviceOnline
			}

			SettingsListHeader {
				text: "Status"
				preferredVisible: root.serviceOnline
			}

			ListText {
				text: "Active BMS"
				dataItem.uid: root.serviceUid + "/ActiveBmsName"
				preferredVisible: dataItem.valid
			}

			ListText {
				text: "Standby BMS"
				dataItem.uid: root.serviceUid + "/StandbyBmsName"
				preferredVisible: dataItem.valid
			}

			ListText {
				text: "Active profile"
				dataItem.uid: root.serviceUid + "/ActiveBms"
				preferredVisible: dataItem.valid
			}

			ListText {
				text: CommonWords.state
				secondaryText: root.busy ? "Switching..." : "Idle"
				preferredVisible: root.serviceOnline
			}

			ListText {
				text: "Last switch"
				dataItem.uid: root.serviceUid + "/LastSwitchReason"
				preferredVisible: dataItem.valid
			}

			SettingsListHeader {
				text: "SOC limits"
				preferredVisible: root.serviceOnline
			}

			ListSwitch {
				text: "Dynamic minimum SOC"
				secondaryText: "ESS active SOC limit + 5%"
				checked: root.dynamicMinSoc
				interactive: minSocItem.valid
				updateDataOnClick: false
				preferredVisible: minSocItem.valid
				onClicked: {
					if (checked) {
						// Leave dynamic mode: seed the fixed limit with the currently effective one.
						const seed = (activeMinSocItem.valid && activeMinSocItem.value >= 0)
								? Math.round(activeMinSocItem.value) : 20
						const limit = maxSocItem.valid ? Math.min(seed, maxSocItem.value - 1) : seed
						minSocItem.setValue(limit)
					} else {
						minSocItem.setValue(-1)
					}
				}
			}

			ListSpinBox {
				text: "Minimum SOC (switch when discharging)"
				dataItem.uid: root.serviceUid + "/Settings/BmsSwitcher/MinSoc"
				preferredVisible: dataItem.valid && !root.dynamicMinSoc
				suffix: "%"
				decimals: 0
				from: 0
				to: maxSocItem.valid ? maxSocItem.value - 1 : 99
				stepSize: 1
			}

			ListQuantity {
				text: "Effective minimum SOC"
				value: activeMinSocItem.valid && activeMinSocItem.value >= 0 ? activeMinSocItem.value : NaN
				unit: VenusOS.Units_Percentage
				decimals: 0
				preferredVisible: activeMinSocItem.valid && root.dynamicMinSoc
			}

			ListSpinBox {
				text: "Maximum SOC (switch when charging)"
				dataItem.uid: root.serviceUid + "/Settings/BmsSwitcher/MaxSoc"
				preferredVisible: dataItem.valid
				suffix: "%"
				decimals: 0
				from: {
					const minLimit = root.dynamicMinSoc
							? (activeMinSocItem.valid ? activeMinSocItem.value : -1)
							: (minSocItem.valid ? minSocItem.value : -1)
					return Math.max(1, Math.floor(minLimit) + 1)
				}
				to: 100
				stepSize: 1
			}

			SettingsListHeader {
				text: "Alarm trigger"
				preferredVisible: root.serviceOnline
			}

			ListSwitch {
				text: "Switch on BMS alarm"
				secondaryText: "Voltage, cell voltage or temperature alarm for 30 s"
				dataItem.uid: root.serviceUid + "/Settings/BmsSwitcher/AlarmSwitch"
				preferredVisible: dataItem.valid
			}

			ListText {
				text: "Active BMS alarms"
				dataItem.uid: root.serviceUid + "/ActiveBmsAlarms"
				secondaryText: dataItem.valid && dataItem.value !== "" ? dataItem.value : "None"
				preferredVisible: dataItem.valid
			}

			ListText {
				text: "Standby BMS"
				secondaryText: "Was in alarm when left - no auto switch back"
				preferredVisible: standbyAlarmItem.valid && standbyAlarmItem.value === 1
			}

			SettingsListHeader {
				text: "Manual control"
				preferredVisible: root.serviceOnline
			}

			ListButton {
				text: "Switch to other BMS"
				secondaryText: root.busy ? "Switching..." : "Switch"
				preferredVisible: triggerItem.valid
				enabled: !root.busy
				onClicked: Global.dialogLayer.open(confirmSwitchComponent)

				Component {
					id: confirmSwitchComponent

					ModalWarningDialog {
						dialogDoneOptions: VenusOS.ModalDialog_DoneOptions_OkAndCancel
						title: "Switch BMS now?"
						description: "The DC bus is isolated (DVCC max charge current 0 A), the active BMS driver is stopped and the other one started. This takes up to a few minutes."
						acceptText: "Switch"
						onAccepted: triggerItem.setValue(1)
					}
				}
			}
		}
	}
}
