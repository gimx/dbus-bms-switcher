#!/usr/bin/env python3
import os
import time
import logging
import subprocess
import threading
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

logging.basicConfig(level=logging.INFO, format='[BMS-SWITCHER] %(asctime)s - %(message)s')

# --- Path Configurations ---
BMS_CONFIG_A = "/data/vds/dbus-serialbattery/config.ubms.ini"
BMS_CONFIG_B = "/data/vds/dbus-serialbattery/config.daly.ini"
BMS_TARGET = "/data/vds/dbus-serialbattery/config.ini"

SERVICE_A = "/service/dbus-canbattery.can0"
SERVICE_B = "/service/dbus-serialbattery.ttyUSB0"

SERIAL_DBUS_SERVICE = "com.victronenergy.battery.ttyUSB0"
CAN_DBUS_SERVICE = "com.victronenergy.battery.can0"

SCRIPT_DIR = "/data/vds/dbus-serialbattery"
ENABLE_CMD = os.path.join(SCRIPT_DIR, "enable.sh")
DISABLE_CMD = os.path.join(SCRIPT_DIR, "disable.sh")

class BmsSwitcherService(dbus.service.Object):
    def __init__(self):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        bus_name = dbus.service.BusName("com.victronenergy.bmsswitcher", self.bus)
        super().__init__(bus_name, "/Settings/BmsSwitcher")
        
        self.is_busy = False
        
        # Start SOC periodic monitor loop (checks every 10 seconds)
        GLib.timeout_add_seconds(10, self._check_soc_thresholds)
        logging.info("BMS Switcher Python D-Bus daemon initialized with SOC monitoring.")

    @dbus.service.method("com.victronenergy.bmsswitcher", in_signature='i', out_signature='b')
    def TriggerSwitch(self, val):
        if val == 1:
            if self.is_busy:
                logging.warning("Switch transition already in progress. Request ignored.")
                return False
            # Run the task in a separate thread to keep the D-Bus main loop non-blocking
            threading.Thread(target=self._execute_switch_process, daemon=True).start()
            return True
        return False

    def _get_dbus_value(self, service_name, path):
        try:
            obj = self.bus.get_object(service_name, path)
            iface = dbus.Interface(obj, "com.victronenergy.BusItem")
            return iface.GetValue()
        except Exception:
            return None

    def _set_dbus_value(self, service_name, path, value):
        try:
            obj = self.bus.get_object(service_name, path)
            iface = dbus.Interface(obj, "com.victronenergy.BusItem")
            iface.SetValue(value)
            return True
        except Exception as e:
            logging.error(f"Failed to set {path} on {service_name}: {e}")
            return False

    def _get_vebus_service(self):
        for name in self.bus.list_names():
            if str(name).startswith("com.victronenergy.vebus"):
                return str(name)
        return None

    def _check_soc_thresholds(self):
        """Monitors system SOC and triggers switchover based on charge/discharge conditions."""
        if self.is_busy:
            return True  # Skip check while a switch is currently active

        soc = self._get_dbus_value("com.victronenergy.system", "/Dc/Soc")
        soc_limit = self._get_dbus_value("com.victronenergy.system", "/Control/ActiveSocLimit")
        battery_power = self._get_dbus_value("com.victronenergy.system", "/Dc/Battery/Power")

        if soc is None or battery_power is None:
            return True

        try:
            soc_val = float(soc)
            power_val = float(battery_power)

            # DISCHARGING CONDITION (Negative Power)
            if power_val < -10 and soc_limit is not None:
                target_limit = float(soc_limit) + 5.0
                if soc_val <= target_limit:
                    logging.info(
                        f"Auto-switch triggered (Discharging): Current SOC ({soc_val:.1f}%) "
                        f"reached threshold ({target_limit:.1f}% = Limit {soc_limit}% + 5%)."
                    )
                    self.TriggerSwitch(1)

            # CHARGING CONDITION (Positive Power)
            elif power_val > 10 and soc_val >= 85.0:
                logging.info(
                    f"Auto-switch triggered (Charging): Current SOC ({soc_val:.1f}%) "
                    f"reached 85% threshold."
                )
                self.TriggerSwitch(1)

        except Exception as e:
            logging.error(f"Error processing SOC threshold logic: {e}")

        return True  # Returning True keeps the GLib timer alive

    def _execute_switch_process(self):
        self.is_busy = True
        try:
            # 1. Determine profile state
            current_real_path = os.path.realpath(BMS_TARGET) if os.path.exists(BMS_TARGET) else ""
            
            if current_real_path == BMS_CONFIG_A:
                new_profile = BMS_CONFIG_B
                old_service = SERVICE_A
                new_service = SERVICE_B
                transition = "CAN -> Serial"
                expected_bms = CAN_DBUS_SERVICE
            else:
                new_profile = BMS_CONFIG_A
                old_service = SERVICE_B
                new_service = SERVICE_A
                transition = "Serial -> CAN"
                expected_bms = SERIAL_DBUS_SERVICE

            logging.info(f"Detected profile: {current_real_path or 'Unknown/Missing'}")
            logging.info(f"Initiating transition: {transition}")

            # 2. Verify Active BMS on D-Bus
            logging.info("Verifying active BMS service on DBus...")
            active_bms = self._get_dbus_value("com.victronenergy.system", "/ActiveBmsService")
            active_bms_str = str(active_bms).strip("'\"[] ") if active_bms is not None else ""

            if not active_bms_str:
                logging.warning("No active BMS detected on DBus. Proceeding with switch anyway...")
            elif active_bms_str != expected_bms:
                logging.error(f"Active BMS ({active_bms_str}) does not match preset ({expected_bms})! Aborting.")
                return
            else:
                logging.info(f"Active BMS verified: {active_bms_str}")

            # 3. Wait up to 10 minutes for Consumption < 100W
            logging.info("Waiting up to 10 minutes for L1 consumption to drop below 100W...")
            timeout = 600
            elapsed = 0
            wait_interval = 5

            while elapsed < timeout:
                power = self._get_dbus_value("com.victronenergy.system", "/Ac/ConsumptionOnOutput/L1/Power")
                if power is not None:
                    try:
                        power_val = int(float(power))
                        if power_val < 100:
                            logging.info(f"Power has dropped to {power_val}W. Proceeding...")
                            break
                    except ValueError:
                        pass
                time.sleep(wait_interval)
                elapsed += wait_interval

            if elapsed >= timeout:
                logging.warning("10-minute timeout reached. Proceeding anyway.")

            # 4. MultiPlus Power OFF
            vebus_service = self._get_vebus_service()
            if vebus_service:
                self._set_dbus_value(vebus_service, "/Mode", 4)
                logging.info(f"MultiPlus ({vebus_service}) set to OFF")

            # 5. Pre-Disable Hooks (Serial BMS)
            if "serialbattery" in old_service:
                names = [str(n) for n in self.bus.list_names()]
                if SERIAL_DBUS_SERVICE in names:
                    logging.info(f"Setting ForceCharge/Discharge Off for Serial BMS ({SERIAL_DBUS_SERVICE})...")
                    self._set_dbus_value(SERIAL_DBUS_SERVICE, "/Settings/ForceChargingOff", 1)
                    self._set_dbus_value(SERIAL_DBUS_SERVICE, "/Settings/ForceDischargingOff", 1)

            # 6. Disable Current Service
            if os.access(DISABLE_CMD, os.X_OK):
                logging.info(f"Disabling {old_service}...")
                subprocess.run([DISABLE_CMD, old_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logging.error(f"Error: {DISABLE_CMD} not executable or missing.")

            # 7. Post-Disable Hooks (CAN BMS)
            if "canbattery" in old_service:
                interface = old_service.split(".")[-1]
                logging.info(f"Sending shutdown frame to {interface} (Post-Disable)...")
                subprocess.run(["cansend", interface, "440#00000000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 8. Swap Symlink
            try:
                if os.path.lexists(BMS_TARGET):
                    os.remove(BMS_TARGET)
                os.symlink(new_profile, BMS_TARGET)
                logging.info(f"Linked {BMS_TARGET} to {os.path.basename(new_profile)}")
            except Exception as e:
                logging.error(f"Failed to update symlink: {e}")

            # 9. Enable Target Service
            if os.access(ENABLE_CMD, os.X_OK):
                logging.info(f"Enabling {new_service}...")
                subprocess.run([ENABLE_CMD, new_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logging.error(f"Error: {ENABLE_CMD} not executable or missing.")

            time.sleep(3)

            # 10. MultiPlus Power ON
            if vebus_service:
                self._set_dbus_value(vebus_service, "/Mode", 3)
                logging.info("MultiPlus set to ON")

            logging.info("BMS Switch Complete.")

        finally:
            self.is_busy = False

if __name__ == "__main__":
    mainloop = GLib.MainLoop()
    service = BmsSwitcherService()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        pass
