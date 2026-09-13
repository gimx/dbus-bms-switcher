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


def get_git_version():
    """Executes git describe to fetch the current git tag/hash of the file directory."""
    try:
        script_dir = os.path.dirname(os.path.realpath(__file__))
        return subprocess.check_output(
            ["git", "describe", "--always", "--dirty"],
            cwd=script_dir,
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
    except Exception:
        return "Unknown"


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
        git_ver = get_git_version()
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path('/Mgmt/ProcessVersion', f"{git_ver} (Python {platform.python_version()})")
        self._dbusservice.add_path('/Mgmt/Connection', 'Internal BMS Switcher Daemon')
        self._dbusservice.add_path('/DeviceInstance', 0)
        self._dbusservice.add_path('/ProductId', 0xFFFF)
        self._dbusservice.add_path('/ProductName', 'BMS Switcher Service')
        self._dbusservice.add_path('/CustomName', 'BMS Switcher')
        self._dbusservice.add_path('/Connected', 1)

        # Settings
        self._dbusservice.add_path('/Settings/BmsSwitcher/MinSoc', 
                                   self.min_soc if self.min_soc is not None else -1.0, 
                                   writeable=True, 
                                   onchangecallback=self._handle_min_soc_change)
        self._dbusservice.add_path('/Settings/BmsSwitcher/MaxSoc', 
                                   self.max_soc, 
                                   writeable=True, 
                                   onchangecallback=self._handle_max_soc_change)
        self._dbusservice.add_path('/Settings/BmsSwitcher/SocLimitSource', self.soc_source, writeable=True)

        # Status & Control Paths (Non-settings)
        self._dbusservice.add_path('/ActiveMinSoc', -1.0)
        self._dbusservice.add_path('/LastSwitchReason', 'None', writeable=True)
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
        logging.info(f"BMS Switcher service registered on D-Bus ({SERVICE_NAME}) [Git Version: {git_ver}].")

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

    def _handle_min_soc_change(self, path, value):
        """Callback executed when MinSoc is updated over D-Bus."""
        try:
            val = float(value)
            if val < 0:
                self.min_soc = None
                logging.info("MinSoc set to dynamic mode (< 0) via D-Bus.")
            else:
                self.min_soc = val
                logging.info(f"MinSoc updated via D-Bus to: {self.min_soc}%")

            self._dbusservice['/Settings/BmsSwitcher/SocLimitSource'] = "D-Bus"
            return True
        except (ValueError, TypeError) as e:
            logging.error(f"Invalid MinSoc value received: {value} ({e})")
            return False

    def _handle_max_soc_change(self, path, value):
        """Callback executed when MaxSoc is updated over D-Bus."""
        try:
            val = float(value)
            self.max_soc = val
            logging.info(f"MaxSoc updated via D-Bus to: {self.max_soc}%")

            self._dbusservice['/Settings/BmsSwitcher/SocLimitSource'] = "D-Bus"
            return True
        except (ValueError, TypeError) as e:
            logging.error(f"Invalid MaxSoc value received: {value} ({e})")
            return False

    def _handle_trigger_switch(self, path, value):
        if int(value) == 1:
            if self.is_busy:
                logging.warning("Switch transition already in progress. Request ignored.")
                self._dbusservice['/TriggerSwitch'] = 0
                return False
            threading.Thread(target=self._execute_switch_process, daemon=True).start()
            return True
        return True

    def _get_dbus_value(self, service_name, path):
        try:
            obj = self.bus.get_object(service_name, path)
            return dbus.Interface(obj, "com.victronenergy.BusItem").GetValue()
        except Exception:
            return None

    def _set_dbus_value(self, service_name, path, value):
        """Sets a D-Bus value using explicit dbus types to ensure compatibility."""
        try:
            obj = self.bus.get_object(service_name, path)
            iface = dbus.Interface(obj, "com.victronenergy.BusItem")
            
            if isinstance(value, float):
                dbus_val = dbus.Double(value)
            elif isinstance(value, int):
                dbus_val = dbus.Double(float(value)) if value < 0 else dbus.Int32(value)
            else:
                dbus_val = value

            iface.SetValue(dbus_val)
            return True
        except Exception as e:
            logging.warning(f"Could not set {path} on {service_name}: {e}")
            return False

    def _get_active_serial_battery_service(self):
        """Dynamically discovers any active dbus-serialbattery service instance."""
        for name in self.bus.list_names():
            name_str = str(name)
            if name_str.startswith("com.victronenergy.battery.tty") or name_str.startswith("com.victronenergy.battery.serial"):
                return name_str
        return None

    def _check_soc_thresholds(self):
        """Monitors SOC and prevents continuous switchover loops when both batteries are low/high."""
        if self.is_busy:
            return True

        soc = self._get_dbus_value("com.victronenergy.system", "/Dc/Battery/Soc")
        soc_limit = self._get_dbus_value("com.victronenergy.system", "/Control/ActiveSocLimit")
        battery_power = self._get_dbus_value("com.victronenergy.system", "/Dc/Battery/Power")

        if soc is None or battery_power is None:
            return True

        try:
            soc_val = float(soc)
            power_val = float(battery_power)
            now = time.time()

            if self.min_soc is not None:
                target_min_limit = float(self.min_soc)
            elif soc_limit is not None:
                target_min_limit = float(soc_limit) + 5.0
            else:
                target_min_limit = None

            # Publish currently active minimum SOC threshold to D-Bus
            self._dbusservice['/ActiveMinSoc'] = float(target_min_limit) if target_min_limit is not None else -1.0

            target_max_limit = float(self.max_soc)

            # Hysteresis resets
            if self.last_auto_switch_reason == "LOW_SOC" and target_min_limit is not None:
                if soc_val > (target_min_limit + 5.0):
                    logging.info("SOC recovered above minimum threshold + 5%. Clearing LOW_SOC switch lock.")
                    self.last_auto_switch_reason = None

            if self.last_auto_switch_reason == "HIGH_SOC" and soc_val < (target_max_limit - 5.0):
                logging.info(f"SOC dropped below {target_max_limit - 5.0:.1f}%. Clearing HIGH_SOC switch lock.")
                self.last_auto_switch_reason = None

            if (now - self.last_auto_switch_time) < self.COOLDOWN_SECONDS:
                return True

            # Discharging Logic (Trigger if SOC <= limit AND not actively charging >10W)
            if power_val <= 10 and target_min_limit is not None:
                if soc_val <= target_min_limit:
                    if self.last_auto_switch_reason == "LOW_SOC":
                        logging.warning(f"Both battery banks are low ({soc_val:.1f}% <= {target_min_limit:.1f}%). Loop prevented.")
                    else:
                        reason_msg = f"Low SOC threshold reached ({soc_val:.1f}% <= {target_min_limit:.1f}%)"
                        logging.info(f"Auto-switch triggered (Discharging/Idle): {reason_msg}")
                        self.last_auto_switch_reason = "LOW_SOC"
                        self.last_auto_switch_time = now
                        self._dbusservice['/LastSwitchReason'] = reason_msg
                        self._handle_trigger_switch('/TriggerSwitch', 1)

            # Charging Logic (Trigger if SOC >= limit AND not actively discharging >10W)
            elif power_val >= -10 and soc_val >= target_max_limit:
                if self.last_auto_switch_reason == "HIGH_SOC":
                    logging.warning(f"Both battery banks are full ({soc_val:.1f}% >= {target_max_limit:.1f}%). Loop prevented.")
                else:
                    reason_msg = f"High SOC threshold reached ({soc_val:.1f}% >= {target_max_limit:.1f}%)"
                    logging.info(f"Auto-switch triggered (Charging/Idle): {reason_msg}")
                    self.last_auto_switch_reason = "HIGH_SOC"
                    self.last_auto_switch_time = now
                    self._dbusservice['/LastSwitchReason'] = reason_msg
                    self._handle_trigger_switch('/TriggerSwitch', 1)

        except Exception as e:
            logging.error(f"Error processing SOC threshold logic: {e}")

        return True

    def _execute_switch_process(self):
        self.is_busy = True
        try:
            current_real_path = os.path.realpath(self.bms_target) if os.path.exists(self.bms_target) else ""
            
            if current_real_path == self.bms_config_a:
                new_profile = self.bms_config_b
                old_service = SERVICE_A
                new_service = SERVICE_B
                transition = "CAN -> Serial"
            else:
                new_profile = self.bms_config_a
                old_service = SERVICE_B
                new_service = SERVICE_A
                transition = "Serial -> CAN"

            logging.info(f"Detected profile: {current_real_path or 'Unknown/Missing'}")
            logging.info(f"Initiating seamless pass-through switch ({transition})...")

            if not self.last_auto_switch_time or (time.time() - self.last_auto_switch_time > 10):
                self._dbusservice['/LastSwitchReason'] = f"Manual Trigger ({transition})"

            # 1. Seamless AC Pass-Through Isolation
            logging.info("Isolating DC Bus: Setting DVCC Max Charge Current to 0A...")
            self._set_dbus_value("com.victronenergy.settings", "/Settings/SystemSetup/MaxChargeCurrent", 0.0)

            serial_service_name = self._get_active_serial_battery_service()
            if serial_service_name:
                logging.info(f"Forcing Charge & Discharge Off on {serial_service_name}...")
                self._set_dbus_value(serial_service_name, "/Settings/ForceDischargingOff", 1)
                self._set_dbus_value(serial_service_name, "/Settings/ForceChargingOff", 1)

            time.sleep(2)

            # 2. Disable Current BMS Service
            if os.access(self.disable_cmd, os.X_OK):
                logging.info(f"Disabling {old_service}...")
                subprocess.run([self.disable_cmd, old_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logging.error(f"Error: {self.disable_cmd} not executable or missing.")

            # 3. Post-Disable Hooks (CAN BMS Shutdown Frame)
            if "canbattery" in old_service:
                interface = old_service.split(".")[-1]
                logging.info(f"Sending shutdown frame to {interface}...")
                subprocess.run(["cansend", interface, "440#00000000"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # 4. Swap Symlink Profile Target
            try:
                if os.path.lexists(self.bms_target):
                    os.remove(self.bms_target)
                os.symlink(new_profile, self.bms_target)
                logging.info(f"Linked {self.bms_target} to {os.path.basename(new_profile)}")
            except Exception as e:
                logging.error(f"Failed to update symlink: {e}")

            # 5. Enable Target Service
            if os.access(self.enable_cmd, os.X_OK):
                logging.info(f"Enabling {new_service}...")
                subprocess.run([self.enable_cmd, new_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logging.error(f"Error: {self.enable_cmd} not executable or missing.")

            # 6. Restart serial-starter when switching to Serial BMS
            if "serialbattery" in new_service:
                logging.info("Restarting serial-starter service...")
                if os.path.exists("/service/serial-starter"):
                    subprocess.run(["svc", "-t", "/service/serial-starter"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                logging.info("Waiting for Serial BMS to appear on D-Bus...")
                serial_service_name = None
                for _ in range(15):
                    serial_service_name = self._get_active_serial_battery_service()
                    if serial_service_name:
                        break
                    time.sleep(1)

            time.sleep(2)

            # 7. Restore DVCC & BMS Current Control
            serial_service_name = self._get_active_serial_battery_service()
            if serial_service_name:
                logging.info(f"Re-enabling Charge & Discharge on {serial_service_name}...")
                self._set_dbus_value(serial_service_name, "/Settings/ForceDischargingOff", 0)
                self._set_dbus_value(serial_service_name, "/Settings/ForceChargingOff", 0)

            logging.info("Re-enabling DC Bus charge current limits on DVCC...")
            self._set_dbus_value("com.victronenergy.settings", "/Settings/SystemSetup/MaxChargeCurrent", -1.0)

            logging.info("Seamless BMS Pass-Through Switch Complete.")

        except Exception as e:
            logging.error(f"Error during execution of switch process: {e}")
        finally:
            # Reset trigger back to 0 upon completion of switch process
            self._dbusservice['/TriggerSwitch'] = 0
            self.is_busy = False


if __name__ == "__main__":
    DBusGMainLoop(set_as_default=True)

    mainloop = GLib.MainLoop()
    service = BmsSwitcherService()
    try:
        mainloop.run()
    except KeyboardInterrupt:
        pass
