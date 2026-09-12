#!/usr/bin/env python3
import os
import sys
import time
import logging
import subprocess
import threading
import configparser
import platform
from gi.repository import GLib

# Victron Venus OS D-Bus library imports
sys.path.insert(1, os.path.join(os.path.dirname(os.path.realpath(__file__)), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService
import dbus
from dbus.mainloop.glib import DBusGMainLoop

logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

SERVICE_NAME = "com.victronenergy.bmsswitcher"

SERVICE_A = "/service/dbus-canbattery.can0"
SERVICE_B = "/service/dbus-serialbattery.ttyUSB0"
SERIAL_DBUS_SERVICE = "com.victronenergy.battery.ttyUSB0"
CAN_DBUS_SERVICE = "com.victronenergy.battery.can0"


class BmsSwitcherService:
    def __init__(self):
        # Read config using flat headerless template logic
        config = self._getConfig()

        self.base_path = config['DEFAULT'].get('SERIALBATTERY_BASE_PATH', '/data/apps').strip().rstrip('/')
        
        raw_min = config['DEFAULT'].get('MIN_SOC', '').strip()
        raw_max = config['DEFAULT'].get('MAX_SOC', '85.0').strip()
        
        self.min_soc = float(raw_min) if raw_min else None
        self.max_soc = float(raw_max) if raw_max else 85.0
        self.soc_source = "Config" if (self.min_soc is not None or raw_min) else "Default"

        # Derived paths
        self.bms_config_a = os.path.join(self.base_path, "dbus-serialbattery/config.ubms.ini")
        self.bms_config_b = os.path.join(self.base_path, "dbus-serialbattery/config.daly.ini")
        self.bms_target = os.path.join(self.base_path, "dbus-serialbattery/config.ini")
        self.script_dir = os.path.join(self.base_path, "dbus-serialbattery")
        self.enable_cmd = os.path.join(self.script_dir, "enable.sh")
        self.disable_cmd = os.path.join(self.script_dir, "disable.sh")

        logging.info(f"Base Path: {self.base_path}")
        logging.info(f"Script Dir: {self.script_dir}")
        logging.info(f"SOC Limits Loaded -> MinSOC: {self.min_soc if self.min_soc is not None else 'Dynamic'}, MaxSOC: {self.max_soc}% (Source: {self.soc_source})")

        # Initialize D-Bus System Bus and VeDbusService
        self.bus = dbus.SystemBus()
        self._dbusservice = VeDbusService(SERVICE_NAME, self.bus, register=False)

        # Management & Mandatory D-Bus Objects
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path('/Mgmt/ProcessVersion', '1.0, running on Python ' + platform.python_version())
        self._dbusservice.add_path('/Mgmt/Connection', 'Internal BMS Switcher Daemon')
        self._dbusservice.add_path('/DeviceInstance', 0)
        self._dbusservice.add_path('/ProductId', 0xFFFF)
        self._dbusservice.add_path('/ProductName', 'BMS Switcher Service')
        self._dbusservice.add_path('/CustomName', 'BMS Switcher')
        self._dbusservice.add_path('/Connected', 1)

        # Exposed Settings & Switch Reason
        self._dbusservice.add_path('/Settings/BmsSwitcher/MinSoc', self.min_soc if self.min_soc is not None else -1.0)
        self._dbusservice.add_path('/Settings/BmsSwitcher/MaxSoc', self.max_soc)
        self._dbusservice.add_path('/Settings/BmsSwitcher/SocLimitSource', self.soc_source)
        self._dbusservice.add_path('/Settings/BmsSwitcher/LastSwitchReason', 'None', writeable=True)

        # Trigger Method callback hook
        self._dbusservice.add_path('/TriggerSwitch', 0, writeable=True, onchangecallback=self._handle_trigger_switch)

        # Operational state & anti-flip-flop tracking
        self.is_busy = False
        self.last_auto_switch_reason = None
        self.last_auto_switch_time = 0
        self.COOLDOWN_SECONDS = 900  # 15-minute minimum cooldown between auto-switches

        # Register the D-Bus service
        self._dbusservice.register()

        # Start periodic SOC monitoring loop (checks every 10 seconds)
        GLib.timeout_add_seconds(10, self._check_soc_thresholds)
        logging.info(f"BMS Switcher service registered on D-Bus ({SERVICE_NAME}) with SOC anti-flip-flop protection.")

    def _getConfig(self):
        config = configparser.ConfigParser()
        config_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.ini")
        if not os.path.exists(config_file):
            config_file = "/data/apps/dbus-bmsswitcher/config.ini"

        if os.path.exists(config_file):
            logging.info(f"Loading service configuration from: {config_file}")
            try:
                with open(config_file, 'r') as f:
                    config.read_string("[DEFAULT]\n" + f.read())
            except Exception as e:
                logging.error(f"Error reading configuration file {config_file}: {e}")
        else:
            logging.info("No configuration file found. Using default paths and SOC limits.")
            
        return config

    def _handle_trigger_switch(self, path, value):
        if int(value) == 1:
            if self.is_busy:
                logging.warning("Switch transition already in progress. Request ignored.")
                return False
            threading.Thread(target=self._execute_switch_process, daemon=True).start()
            # Reset trigger path state
            self._dbusservice['/TriggerSwitch'] = 0
            return True
        return True

    def _get_dbus_value(self, service_name, path):
        try:
            obj = self.bus.get_object(service_name, path)
            return dbus.Interface(obj, "com.victronenergy.BusItem").GetValue()
        except Exception:
            return None

    def _set_dbus_value(self, service_name, path, value):
        try:
            obj = self.bus.get_object(service_name, path)
            dbus.Interface(obj, "com.victronenergy.BusItem").SetValue(value)
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
        """Monitors SOC and prevents continuous switchover loops when both batteries are low/high."""
        if self.is_busy:
            return True

        soc = self._get_dbus_value("com.victronenergy.system", "/Dc/Soc")
        soc_limit = self._get_dbus_value("com.victronenergy.system", "/Control/ActiveSocLimit")
        battery_power = self._get_dbus_value("com.victronenergy.system", "/Dc/Battery/Power")

        if soc is None or battery_power is None:
            return True

        try:
            soc_val = float(soc)
            power_val = float(battery_power)
            now = time.time()

            # Dynamic low SOC threshold: override from config if present, otherwise soc_limit + 5.0
            if self.min_soc is not None:
                target_min_limit = float(self.min_soc)
            elif soc_limit is not None:
                target_min_limit = float(soc_limit) + 5.0
            else:
                target_min_limit = None

            target_max_limit = float(self.max_soc)

            # --- HYSTERESIS RESETS ---
            # Reset LOW_SOC lock once the active battery charges above threshold + 5%
            if self.last_auto_switch_reason == "LOW_SOC" and target_min_limit is not None:
                if soc_val > (target_min_limit + 5.0):
                    logging.info("SOC recovered above minimum threshold + 5%. Clearing LOW_SOC switch lock.")
                    self.last_auto_switch_reason = None

            # Reset HIGH_SOC lock once the active battery discharges below target_max_limit - 5%
            if self.last_auto_switch_reason == "HIGH_SOC" and soc_val < (target_max_limit - 5.0):
                logging.info(f"SOC dropped below {target_max_limit - 5.0:.1f}%. Clearing HIGH_SOC switch lock.")
                self.last_auto_switch_reason = None

            # Enforce global cooldown period
            if (now - self.last_auto_switch_time) < self.COOLDOWN_SECONDS:
                return True

            # --- DISCHARGING LOGIC ---
            if power_val < -10 and target_min_limit is not None:
                if soc_val <= target_min_limit:
                    if self.last_auto_switch_reason == "LOW_SOC":
                        logging.warning(
                            f"Both battery banks are low (SOC {soc_val:.1f}% <= threshold {target_min_limit:.1f}%). "
                            "Preventing continuous switch loop."
                        )
                    else:
                        reason_msg = f"Low SOC threshold reached ({soc_val:.1f}% <= {target_min_limit:.1f}%)"
                        logging.info(f"Auto-switch triggered (Discharging): {reason_msg}")
                        self.last_auto_switch_reason = "LOW_SOC"
                        self.last_auto_switch_time = now
                        self._dbusservice['/Settings/BmsSwitcher/LastSwitchReason'] = reason_msg
                        self._handle_trigger_switch('/TriggerSwitch', 1)

            # --- CHARGING LOGIC ---
            elif power_val > 10 and soc_val >= target_max_limit:
                if self.last_auto_switch_reason == "HIGH_SOC":
                    logging.warning(
                        f"Both battery banks are full (SOC {soc_val:.1f}% >= {target_max_limit:.1f}%). "
                        "Preventing continuous switch loop."
                    )
                else:
                    reason_msg = f"High SOC threshold reached ({soc_val:.1f}% >= {target_max_limit:.1f}%)"
                    logging.info(f"Auto-switch triggered (Charging): {reason_msg}")
                    self.last_auto_switch_reason = "HIGH_SOC"
                    self.last_auto_switch_time = now
                    self._dbusservice['/Settings/BmsSwitcher/LastSwitchReason'] = reason_msg
                    self._handle_trigger_switch('/TriggerSwitch', 1)

        except Exception as e:
            logging.error(f"Error processing SOC threshold logic: {e}")

        return True

    def _execute_switch_process(self):
        self.is_busy = True
        try:
            # 1. Determine profile state
            current_real_path = os.path.realpath(self.bms_target) if os.path.exists(self.bms_target) else ""
            
            if current_real_path == self.bms_config_a:
                new_profile = self.bms_config_b
                old_service = SERVICE_A
                new_service = SERVICE_B
                transition = "CAN -> Serial"
                expected_bms = CAN_DBUS_SERVICE
            else:
                new_profile = self.bms_config_a
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
            elif expected_bms not in active_bms_str and active_bms_str not in expected_bms:
                logging.error(f"Active BMS ({active_bms_str}) does not match preset ({expected_bms})! Aborting.")
                return
            else:
                logging.info(f"Active BMS verified: {active_bms_str}")

            # 3. Check MultiPlus Mode & Wait for Consumption < 100W
            vebus_service = self._get_vebus_service()
            vebus_mode = self._get_dbus_value(vebus_service, "/Mode") if vebus_service else None
            vebus_state = self._get_dbus_value(vebus_service, "/State") if vebus_service else None
 
            if vebus_mode == 4 or vebus_state is None:
                logging.info("MultiPlus is already OFF. Skipping power consumption wait.")
            else:
                logging.info("Waiting up to 10 minutes for L1 consumption to drop below 100W...")
                timeout = 600
                elapsed = 0
                wait_interval = 5

                while elapsed < timeout:
                    vebus_mode = self._get_dbus_value(vebus_service, "/Mode") if vebus_service else None
                    if vebus_mode == 4:
                        logging.info("MultiPlus switched to OFF during wait. Proceeding...")
                        break

                    power = self._get_dbus_value("com.victronenergy.system", "/Ac/ConsumptionOnOutput/L1/Power")
                    
                    if power is not None and not isinstance(power, (dbus.Array, list, dict, tuple)):
                        try:
                            power_val = int(float(power))
                            if power_val < 100:
                                logging.info(f"Power has dropped to {power_val}W. Proceeding...")
                                break
                        except (ValueError, TypeError):
                            pass

                    time.sleep(wait_interval)
                    elapsed += wait_interval

                if elapsed >= timeout:
                    logging.warning("10-minute timeout reached. Proceeding anyway.")

            # 4. MultiPlus Power OFF
            if vebus_service:
                self._set_dbus_value(vebus_service, "/Mode", 4)
                logging.info(f"MultiPlus ({vebus_service}) set to OFF")

            # 5. Pre-Disable Hooks (Serial BMS)
            if "serialbattery" in old_service:
                names = [str(n) for n in self.bus.list_names()]
                if SERIAL_DBUS_SERVICE in names:
                    logging.info(f"Setting ForceCharge/Discharge Off for Serial BMS ({SERIAL_DBUS_SERVICE})...")
                    self._set_dbus_value(SERIAL_DBUS_SERVICE, "/Settings/ForceChargingOff", 1)

            # 6. Disable Current Service
            if os.access(self.disable_cmd, os.X_OK):
                logging.info(f"Disabling {old_service}...")
                subprocess.run([self.disable_cmd, old_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logging.error(f"Error: {self.disable_cmd} not executable or missing.")

            # 7. Post-Disable Hooks (CAN BMS)
            if "canbattery" in old_service:
                interface = old_service.split(".")[-1]
                logging.info(f"Sending shutdown frame to {interface} (Post-Disable)...")
                subprocess.run(["cansend", interface, "440#00000000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 8. Swap Symlink
            try:
                if os.path.lexists(self.bms_target):
                    os.remove(self.bms_target)
                os.symlink(new_profile, self.bms_target)
                logging.info(f"Linked {self.bms_target} to {os.path.basename(new_profile)}")
            except Exception as e:
                logging.error(f"Failed to update symlink: {e}")

            # 9. Enable Target Service
            if os.access(self.enable_cmd, os.X_OK):
                logging.info(f"Enabling {new_service}...")
                subprocess.run([self.enable_cmd, new_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logging.error(f"Error: {self.enable_cmd} not executable or missing.")

            # 9b. Restart serial-starter when switching to Serial BMS
            if "serialbattery" in new_service:
                logging.info("Restarting serial-starter service for Serial BMS...")
                if os.path.exists("/service/serial-starter"):
                    subprocess.run(["svc", "-t", "/service/serial-starter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 9c. Post-Enable Hooks (Serial BMS: Re-enable Charge/Discharge)
            if "serialbattery" in new_service:
                logging.info(f"Waiting for Serial BMS ({SERIAL_DBUS_SERVICE}) to appear on D-Bus...")
                for _ in range(15):
                    names = [str(n) for n in self.bus.list_names()]
                    if SERIAL_DBUS_SERVICE in names:
                        break
                    time.sleep(1)

                logging.info(f"Re-enabling Charge and Discharge for Serial BMS ({SERIAL_DBUS_SERVICE})...")
                self._set_dbus_value(SERIAL_DBUS_SERVICE, "/Settings/ForceChargingOff", 0)

            time.sleep(3)

            # 10. MultiPlus Power ON
            if vebus_service:
                self._set_dbus_value(vebus_service, "/Mode", 3)
                logging.info("MultiPlus set to ON")

            logging.info("BMS Switch Complete.")

        finally:
            self.is_busy = False


if __name__ == "__main__":
    # Crucial: Initialize D-Bus main loop bindings BEFORE instantiating any VeDbusService
    DBusGMainLoop(set_as_default=True)

    mainloop = GLib.MainLoop()
    service = BmsSwitcherService()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        pass
