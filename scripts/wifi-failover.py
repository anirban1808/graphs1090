#!/usr/bin/env python3
# Advanced WiFi Failover Script for Raspberry Pi

import subprocess
import time
import logging
import logging.handlers
import re
import json
import os
import sys
import tempfile

# --- Configuration ---
# SSIDs (Ensure these are exactly as in your wpa_supplicant.conf)
PRIMARY_REPEATER_SSID = "Tenda_Misshka"
SECONDARY_BACKUP_SSID = "MisshkaTel"
MASTER_SOURCE_SSID = "SEN147w"

# Connectivity Check
PING_TARGETS = ["8.8.8.8", "1.1.1.1", "9.9.9.9"]
PING_TIMEOUT_SECONDS = 5

# --- Intervals & Durations (in seconds) ---
CHECK_INTERVAL_SECONDS = 5 * 60  # 5 minutes - The script's main heartbeat
RESTORATION_CHECK_INTERVAL = 30 * 60 # 30 minutes
TIME_ON_SECONDARY_TO_CHECK_MASTER_DURATION = 3 * 60 * 60  # 3 hours
TIME_ON_MASTER_OVERRIDE_MODE_DURATION = 24 * 60 * 60  # 24 hours
MASTER_SOURCE_COOLDOWN_DURATION = 24 * 60 * 60  # 24 hours
CHECK_MISSHKAWIFI_FROM_ACTING_PRIMARY_INTERVAL = 3 * 60 * 60

# --- Command & Settle Timings ---
WPA_CLI_COMMAND_TIMEOUT = 30
WIFI_CONNECTION_SETTLE_TIME = 25

# --- Thresholds (Counts) ---
PRIMARY_REPEATER_FAIL_THRESHOLD = 3
MASTER_SOURCE_FAIL_THRESHOLD = 3
PRIMARY_REPEATER_RESTORE_THRESHOLD = 2

# --- File Paths ---
LOG_FILE_PATH = "/var/log/wifi_failover.log"
STATE_FILE_DIR = "/var/lib/wifi_failover"
STATE_FILE_PATH = os.path.join(STATE_FILE_DIR, "script_state.json")

# --- Wireless Interface ---
WLAN_IFACE = "wlan0"

# --- Mode Constants (Correctly defined) ---
MODE_ON_MISSHKAWIFI = "MODE_ON_MISSHKAWIFI"
MODE_ON_MISSKATEL = "MODE_ON_MISSKATEL"
MODE_ON_SEN147W_MASTER_OVERRIDE = "MODE_ON_SEN147w_MASTER_OVERRIDE"
MODE_ON_SEN147W_ACTING_PRIMARY = "MODE_ON_SEN147w_ACTING_PRIMARY"

# --- Global State Dictionary ---
DEFAULT_STATE = {
    "current_mode": None,
    "misshkawifi_fail_count": 0,
    "misshkawifi_restore_count": 0,
    "sen147w_master_fail_count": 0,
    "time_entered_current_mode_ts": 0,
    "sen147w_cooldown_until_ts": 0,
    "last_check_ts": {
        "check_sen147w_from_misshkatel": 0,
        "check_misshkawifi_from_acting_primary": 0,
        "check_misshkawifi_for_restoration": 0
    }
}
state = {}

# --- Network ID Cache ---
network_ids = {
    PRIMARY_REPEATER_SSID: None,
    SECONDARY_BACKUP_SSID: None,
    MASTER_SOURCE_SSID: None
}

# --- Helper Functions ---
def initialize_logging():
    logger = logging.getLogger()
    if logger.handlers: return
    logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    try:
        os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            LOG_FILE_PATH, when="midnight", interval=1, backupCount=7)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Error setting up file logger for {LOG_FILE_PATH}: {e}")

def run_command(command_parts_list, use_sudo=True):
    if use_sudo and os.geteuid() != 0:
        command_parts_list = ['sudo'] + command_parts_list
    try:
        process = subprocess.run(
            command_parts_list, capture_output=True, text=True, check=False, timeout=WPA_CLI_COMMAND_TIMEOUT)
        if process.returncode != 0:
            logging.error(f"Command '{' '.join(command_parts_list)}' failed with code {process.returncode}: {process.stderr.strip()}")
            return None
        return process.stdout.strip()
    except Exception as e:
        logging.error(f"Error running command '{' '.join(command_parts_list)}': {e}")
        return None

def check_internet(ping_targets=PING_TARGETS):
    for target in ping_targets:
        command = ['ping', '-c', '1', '-W', str(PING_TIMEOUT_SECONDS), target]
        if run_command(command, use_sudo=False) is not None:
            logging.info(f"Internet connectivity OK via {target}.")
            return True
    logging.warning("All ping targets failed. Internet is likely down.")
    return False

def get_current_ssid(interface=WLAN_IFACE):
    output = run_command(['wpa_cli', '-i', interface, 'status'])
    if output:
        ssid_match = re.search(r'^ssid=([^\n]+)', output, re.MULTILINE)
        state_match = re.search(r'^wpa_state=([^\n]+)', output, re.MULTILINE)
        if ssid_match and state_match and state_match.group(1) == "COMPLETED":
            return ssid_match.group(1)
    return None

def get_network_id_from_cli(interface, target_ssid):
    output = run_command(['wpa_cli', '-i', interface, 'list_networks'])
    if output:
        for line in output.split('\n')[1:]:
            parts = line.split('\t')
            if len(parts) >= 2 and parts[1] == target_ssid:
                return parts[0]
    logging.error(f"SSID '{target_ssid}' not found in wpa_supplicant configuration.")
    return None

def switch_to_network(interface, network_id, friendly_ssid_name):
    if not network_id: return False
    run_command(['wpa_cli', '-i', interface, 'enable_network', network_id])
    time.sleep(2)
    select_output = run_command(['wpa_cli', '-i', interface, 'select_network', network_id])
    if select_output and "OK" in select_output:
        time.sleep(WIFI_CONNECTION_SETTLE_TIME)
        if get_current_ssid(interface) == friendly_ssid_name:
            logging.info(f"Successfully switched to and connected to {friendly_ssid_name}.")
            return True
    logging.error(f"Failed to switch to {friendly_ssid_name}.")
    return False

def retry_switch_to_network(ssid_name, max_attempts=3, retry_delay=10):
    network_id = network_ids.get(ssid_name)
    if not network_id: return False
    for attempt in range(1, max_attempts + 1):
        if switch_to_network(WLAN_IFACE, network_id, ssid_name):
            return True
        if attempt < max_attempts:
            time.sleep(retry_delay)
    logging.error(f"All {max_attempts} attempts to switch to {ssid_name} failed.")
    return False

def ensure_wpa_supplicant_responsive(interface=WLAN_IFACE):
    output = run_command(['wpa_cli', '-i', interface, 'ping'])
    if output and "PONG" in output:
        return True
    logging.error("wpa_supplicant is unresponsive.")
    return False

def load_state():
    global state
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r') as f:
                loaded_s = json.load(f)
                state = DEFAULT_STATE.copy()
                state.update(loaded_s)
                logging.info("Successfully loaded state.")
                return
        except Exception as e:
            logging.error(f"Error loading state: {e}. Using defaults.")
    state = DEFAULT_STATE.copy()

def save_state():
    try:
        os.makedirs(os.path.dirname(STATE_FILE_PATH), exist_ok=True)
        with tempfile.NamedTemporaryFile('w', dir=os.path.dirname(STATE_FILE_PATH), delete=False) as tf:
            json.dump(state, tf, indent=4)
            tempname = tf.name
        os.replace(tempname, STATE_FILE_PATH)
    except Exception as e:
        logging.error(f"Error saving state: {e}")

def get_timestamp():
    return time.time()

def handle_mode_on_misshkawifi(current_ssid, internet_ok):
    if current_ssid == PRIMARY_REPEATER_SSID and internet_ok:
        state['misshkawifi_fail_count'] = 0
    else:
        state['misshkawifi_fail_count'] += 1
        logging.warning(f"Primary Repeater Fail Count: {state['misshkawifi_fail_count']}/{PRIMARY_REPEATER_FAIL_THRESHOLD}.")
        if state['misshkawifi_fail_count'] >= PRIMARY_REPEATER_FAIL_THRESHOLD:
            logging.info("Primary Repeater failed threshold. Attempting to switch to Secondary Backup.")
            if retry_switch_to_network(SECONDARY_BACKUP_SSID):
                state['current_mode'] = MODE_ON_MISSKATEL
                state['time_entered_current_mode_ts'] = get_timestamp()
                state['last_check_ts']['check_misshkawifi_for_restoration'] = get_timestamp()
            else:
                logging.error("Failed to switch to Secondary Backup after retries.")

def check_for_restoration(backup_ssid, primary_to_check, restoration_interval):
    time_since_last_check = get_timestamp() - state['last_check_ts'].get("check_misshkawifi_for_restoration", 0)
    if time_since_last_check < restoration_interval:
        logging.info(f"Waiting for restoration check cooldown. {int((restoration_interval - time_since_last_check)/60)} minutes remaining.")
        return
    
    logging.info(f"Restoration check interval passed. Checking {primary_to_check} for recovery...")
    state['last_check_ts']['check_misshkawifi_for_restoration'] = get_timestamp()
    if retry_switch_to_network(primary_to_check):
        internet_restored = check_internet()
        if internet_restored:
            state['misshkawifi_restore_count'] += 1
            if state['misshkawifi_restore_count'] >= PRIMARY_REPEATER_RESTORE_THRESHOLD:
                state['current_mode'] = MODE_ON_MISSHKAWIFI
                logging.info(f"{primary_to_check} successfully restored! Switching to primary mode.")
                return
        else:
            state['misshkawifi_restore_count'] = 0
        
        logging.info(f"Switching back to {backup_ssid} after restoration check.")
        if not retry_switch_to_network(backup_ssid):
            logging.error(f"Failed to switch back to {backup_ssid}. Network state uncertain.")
    else:
        state['misshkawifi_restore_count'] = 0
        if get_current_ssid() != backup_ssid:
            retry_switch_to_network(backup_ssid)

def handle_mode_on_misshkatel(current_ssid, internet_ok):
    if current_ssid != SECONDARY_BACKUP_SSID and not retry_switch_to_network(SECONDARY_BACKUP_SSID):
        return
    if not internet_ok:
        logging.critical("Secondary Backup has NO Internet!")
    
    check_for_restoration(SECONDARY_BACKUP_SSID, PRIMARY_REPEATER_SSID, RESTORATION_CHECK_INTERVAL)
    if state['current_mode'] != MODE_ON_MISSKATEL: return

    time_in_mode = get_timestamp() - state.get('time_entered_current_mode_ts', 0)
    if time_in_mode >= TIME_ON_SECONDARY_TO_CHECK_MASTER_DURATION:
        if state.get('sen147w_cooldown_until_ts', 0) > get_timestamp():
            logging.info(f"Master Source is in cooldown. Skipping check.")
        else:
            if retry_switch_to_network(MASTER_SOURCE_SSID) and check_internet():
                state['current_mode'] = MODE_ON_SEN147W_MASTER_OVERRIDE

def handle_mode_on_sen147w(current_ssid, internet_ok, mode, check_interval):
    if current_ssid != MASTER_SOURCE_SSID and not retry_switch_to_network(MASTER_SOURCE_SSID):
        return
    if not check_internet():
        state['sen147w_master_fail_count'] += 1
        if state['sen147w_master_fail_count'] >= MASTER_SOURCE_FAIL_THRESHOLD:
            logging.warning(f"{MASTER_SOURCE_SSID} failed threshold. Switching to backup.")
            if retry_switch_to_network(SECONDARY_BACKUP_SSID):
                state['current_mode'] = MODE_ON_MISSKATEL
                state['sen147w_cooldown_until_ts'] = get_timestamp() + MASTER_SOURCE_COOLDOWN_DURATION
            return
    else:
        state['sen147w_master_fail_count'] = 0

    time_in_mode = get_timestamp() - state.get('time_entered_current_mode_ts', 0)
    if time_in_mode >= check_interval:
        # This is the corrected restoration check logic for SEN147w modes
        check_for_restoration(MASTER_SOURCE_SSID, PRIMARY_REPEATER_SSID, check_interval)
        if state['current_mode'] == MODE_ON_MISSHKAWIFI:
            return # Switch to primary was successful
        
        # If still in master override mode after the timer, transition to acting primary
        if mode == MODE_ON_SEN147W_MASTER_OVERRIDE:
             state['current_mode'] = MODE_ON_SEN147W_ACTING_PRIMARY
             logging.info("24h Master Override finished. Tenda_Misshka not restored. Transitioning to Acting Primary mode.")

def main():
    initialize_logging()
    logging.info("===== WiFi Failover Script Starting Up =====")
    load_state()

    for ssid in network_ids:
        network_ids[ssid] = get_network_id_from_cli(WLAN_IFACE, ssid)
        if not network_ids[ssid]:
            logging.critical(f"CRITICAL FAILURE: Could not find required SSID '{ssid}'. Exiting.")
            sys.exit(1)

    if state.get('current_mode') is None:
        for ssid in [PRIMARY_REPEATER_SSID, SECONDARY_BACKUP_SSID, MASTER_SOURCE_SSID]:
            if retry_switch_to_network(ssid) and check_internet():
                if ssid == PRIMARY_REPEATER_SSID: state['current_mode'] = MODE_ON_MISSHKAWIFI
                elif ssid == SECONDARY_BACKUP_SSID: state['current_mode'] = MODE_ON_MISSKATEL
                else: state['current_mode'] = MODE_ON_SEN147W_ACTING_PRIMARY
                state['time_entered_current_mode_ts'] = get_timestamp()
                break
        else:
            logging.warning("Failed to connect to any network. Defaulting to Primary mode.")
            state['current_mode'] = MODE_ON_MISSHKAWIFI
        save_state()

    time.sleep(60)

    while True:
        logging.info(f"--- Main Loop Cycle Start (Mode: {state.get('current_mode')}) ---")
        if not ensure_wpa_supplicant_responsive():
            time.sleep(CHECK_INTERVAL_SECONDS)
            continue
        
        current_ssid = get_current_ssid()
        internet_ok = check_internet()
        
        mode_before = state.get('current_mode')
        
        if state['current_mode'] == MODE_ON_MISSHKAWIFI:
            handle_mode_on_misshkawifi(current_ssid, internet_ok)
        elif state['current_mode'] == MODE_ON_MISSKATEL:
            handle_mode_on_misshkatel(current_ssid, internet_ok)
        elif state['current_mode'] == MODE_ON_SEN147W_MASTER_OVERRIDE:
            handle_mode_on_sen147w(current_ssid, internet_ok, MODE_ON_SEN147W_MASTER_OVERRIDE, TIME_ON_MASTER_OVERRIDE_MODE_DURATION)
        elif state['current_mode'] == MODE_ON_SEN147W_ACTING_PRIMARY:
            handle_mode_on_sen147w(current_ssid, internet_ok, MODE_ON_SEN147W_ACTING_PRIMARY, CHECK_MISSHKAWIFI_FROM_ACTING_PRIMARY_INTERVAL)
        else:
            logging.error(f"Unknown mode: '{state.get('current_mode')}'. Resetting.")
            state['current_mode'] = MODE_ON_MISSHKAWIFI

        if mode_before != state.get('current_mode'):
            logging.info(f"Mode changed from '{mode_before}' to '{state.get('current_mode')}'")
            state['time_entered_current_mode_ts'] = get_timestamp()
            state['misshkawifi_restore_count'] = 0
            state['sen147w_master_fail_count'] = 0

        current_connected_ssid = get_current_ssid()
        logging.info(f"✅ Post-check: Connected SSID: {current_connected_ssid} | Mode: {state.get('current_mode')}")

        save_state()
        logging.info(f"--- Cycle End. Sleeping for {CHECK_INTERVAL_SECONDS/60:.1f} mins ---")
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
