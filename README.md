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
