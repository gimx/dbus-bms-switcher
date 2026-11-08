## dbus-bms-switcher
 Service for Victron VenusOS to switch between two BMS both using https://github.com/mr-manuel/venus-os_dbus-serialbattery as driver
 which at time of writing only supports BMS with the same configuration
 In my configuration a 12S LiPo with a serial Daly BMS and a 16S LiFePo with a CANBUS Valence U-BMS would not be supported. 
 This service fixes this by seamlessly switching between both on configurable SOC thresholds or manual trigger.
 Both BMS have to support DC bus decoupling by contactor or FET and proper control and reporting on dbus in 
 Settings/Force(Dis)ChargingOff and Io/AllowTo(Dis)Charge respectively for this to work.
 
 Use this code at your own risk!

 ## Installation
 Run the provided install.sh script.

 ## GUI (gui-v2)
 The installer adds a gui-v2 UI plugin, found under **Settings → Integrations → UI Plugins → BmsSwitcher**.
 It works on the local display and in the remote console / VRM, and no Victron files are modified.
 The page shows the device names of the active and standby BMS, the switch state, lets you edit the minimum SOC (fixed or dynamic) and
 maximum SOC limits, and has a button (with confirmation) that triggers a manual switch.
 SOC limits changed in the GUI are validated (min < max) and written back to `config.ini`.

 The compiled plugin `gui-v2/BmsSwitcher/BmsSwitcher.json` is committed. After editing the QML,
 rebuild it with `gui-v2/build.sh` (needs Qt `rcc`). It targets the gui-v2 v1.3.x plugin API (Venus OS v3.8x).
