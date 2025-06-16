#!/usr/bin/env python # Changed from python3, although collectd itself dictates version
import collectd
import subprocess
import json
import os
import re
import time
import sys # Import sys for version check in logs

# --- Configuration ---
PING_TARGETS = {
    "fr24": "feed.flightradar24.com",
    "fa": "piaware.flightaware.com"
}
FPING_COUNT = 3
FPING_TIMEOUT = 1000
FPING_INTERVAL = 1000
SSID_STATE_FILE = "/var/lib/wifi_failover/script_state.json"

# Map SSIDs to numerical values for RRD storage
SSID_MAPPING = {
    "Tenda_Misshka": 0,
    "MisshkaTel": 1,
    "SEN147w": 2,
    "UNKNOWN": -1
}

PLUGIN_NAME = 'network_monitor'
INTERVAL = 120

# Python 2 compatible logging functions
def log_verbose(msg):
    collectd.debug("{}: {}".format(PLUGIN_NAME, msg))

def log_info(msg):
    collectd.info("{}: {}".format(PLUGIN_NAME, msg))

def log_warning(msg):
    collectd.warning("{}: {}".format(PLUGIN_NAME, msg))

def log_error(msg, exc_info=False):
    if exc_info:
        collectd.error("{}: {}".format(PLUGIN_NAME, msg), exc_info=True)
    else:
        collectd.error("{}: {}".format(PLUGIN_NAME, msg))

def get_latency_fping(target):
    command = [
        'fping', '-q', '-c', str(FPING_COUNT), '-t', str(FPING_TIMEOUT),
        '-i', str(FPING_INTERVAL), target
    ]
    log_verbose("Running fping command: {}".format(' '.join(command)))
    try:
        # subprocess.run is Python 3.5+. For Python 2.x, use subprocess.Popen and communicate.
        # However, collectd's Python binding usually backports some features or we have to use Popen.
        # Let's try Popen for Python 2 compatibility.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, stderr_data = process.communicate()
        stdout = stdout_data.strip()
        stderr = stderr_data.strip()
        log_verbose("fping output for {}: stdout={}, stderr={}".format(target, stdout, stderr))

        if process.returncode == 0 or process.returncode == 1:
            output_to_parse = stderr if stderr else stdout
            match = re.search(r'min/avg/max = [\d.]+/([\d.]+)/[\d.]+', output_to_parse)
            if match:
                avg_latency = float(match.group(1))
                log_info("Latency to {}: {:.1f}ms".format(target, avg_latency))
                return avg_latency
            else:
                log_warning("Could not parse fping output for {}: {}".format(target, output_to_parse))
        else:
            log_error("fping command failed for {} with exit code {}. Output: stdout={}, stderr={}".format(target, process.returncode, stdout, stderr))
    except Exception as e:
        log_error("Error running fping for {}: {}".format(target, e), exc_info=True)
    return None

def get_current_ssid_status():
    if not os.path.exists(SSID_STATE_FILE):
        log_warning("SSID state file not found: {}".format(SSID_STATE_FILE))
        return SSID_MAPPING.get("UNKNOWN")

    try:
        with open(SSID_STATE_FILE, 'r') as f:
            state_data = json.load(f)
            current_mode = state_data.get('current_mode')
            ssid_from_mode = None
            if current_mode == "MODE_ON_MISSHKAWIFI":
                ssid_from_mode = "Tenda_Misshka"
            elif current_mode == "MODE_ON_MISSKATEL":
                ssid_from_mode = "MisshkaTel"
            elif current_mode in ["MODE_ON_SEN147W_MASTER_OVERRIDE", "MODE_ON_SEN147W_ACTING_PRIMARY"]:
                ssid_from_mode = "SEN147w"

            if ssid_from_mode:
                ssid_value = SSID_MAPPING.get(ssid_from_mode, SSID_MAPPING.get("UNKNOWN"))
                log_info("Current SSID from state file: {} (Value: {})".format(ssid_from_mode, ssid_value))
                return ssid_value
            else:
                log_warning("Could not determine SSID from current_mode: {} in {}".format(current_mode, SSID_STATE_FILE))
                return SSID_MAPPING.get("UNKNOWN")

    except (ValueError, IOError) as e: # ValueError for json.JSONDecodeError in Python 2
        log_error("Error reading or parsing SSID state file {}: {}".format(SSID_STATE_FILE, e), exc_info=True)
        return SSID_MAPPING.get("UNKNOWN")
    except Exception as e:
        log_error("Unexpected error in get_current_ssid_status: {}".format(e), exc_info=True)
        return SSID_MAPPING.get("UNKNOWN")


def read_callback():
    log_verbose("Read callback triggered. Python version: {}.{}.{}".format(sys.version_info.major, sys.version_info.minor, sys.version_info.micro))

    for key, target in PING_TARGETS.items():
        avg_latency = get_latency_fping(target)
        if avg_latency is not None:
            val = collectd.Values()
            val.plugin = PLUGIN_NAME              # 'network_monitor'
            val.plugin_instance = key             # 'fr24' or 'fa'
            val.type = 'gauge'
            val.type_instance = 'latency_{}'.format(key)
            val.host = 'localhost'
            val.dispatch(values=[avg_latency])
            log_info("Dispatched latency for {}: {:.1f}ms".format(key, avg_latency))
        else:
            log_warning("No latency value returned for {}".format(key))

    ssid_status_value = get_current_ssid_status()
    if ssid_status_value is not None:
        val = collectd.Values()
        val.plugin = PLUGIN_NAME
        val.plugin_instance = 'ssid'
        val.type = 'gauge'
        val.type_instance = 'ssid_status'
        val.host = 'localhost'
        val.dispatch(values=[ssid_status_value])
        log_info("Dispatched SSID status: {}".format(ssid_status_value))

#        val = collectd.Values(
#            plugin=PLUGIN_NAME,
#            type='gauge',
#            type_instance='ssid_status'
#        )
#        val.dispatch(values=[ssid_status_value])

def init_callback():
    log_info("{} plugin initialized. Python version: {}.{}.{}".format(PLUGIN_NAME, sys.version_info.major, sys.version_info.minor, sys.version_info.micro))

collectd.register_init(init_callback)
collectd.register_read(read_callback, INTERVAL)
#collectd.register_config(None)

