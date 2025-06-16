# graphs1090-upload (Backup by Anirban Sen)

This repository contains a custom backup of the `/usr/share/graphs1090` directory, along with associated scripts and configuration files used for enhanced network and ADS-B monitoring.

## 📁 Directory Structure

### ✅ Core Files
| File / Folder              | Description |
|---------------------------|-------------|
| `graphs1090.sh`           | Modified script to generate custom latency and system graphs using RRDTool. |
| `latency_ssid_monitor.py` | Custom script that pings FlightAware/FR24, writes latency to RRD, and tracks SSID status. |
| `system_stats.py`         | *(See below: captures system metrics like load, memory)* |
| `install.sh`              | Optional install script for graph setup. |
| `wifi-failover.service`   | Systemd service to manage WiFi failover logic. |
| `malarky.conf`            | Local configuration (possibly unused stub). |
| `nginx-graphs1090.conf`   | Nginx config to serve the graphs over HTTP. |

### 🧪 Scripts
| Script                    | Description |
|--------------------------|-------------|
| `rrd-integrate-old.sh`   | Old RRD merge or conversion logic. |
| `rrd_tarscope.sh`        | May generate or compress .rrd graphs. |
| `prune-range.sh`         | Script to trim unwanted data ranges. |
| `reset_python_plugin.py` | Plugin cleanup/reset utility. |
| `test_python_plugin.py`  | Collectd-compatible test plugin. |

### 📁 HTML Frontend
| File                      | Description |
|--------------------------|-------------|
| `html/*.html`            | Templates for graph display. |
| `html/*.css`, `*.js`     | Bootstrap and jQuery dependencies. |
| `html/favicon.png`       | Favicon used in dashboard. |

---

## 🔍 2. `system_stats.py` – Purpose & Timestamp

### 📌 Likely Purpose:
This script most likely collects **CPU load, memory usage, and/or disk usage** and writes them to RRD databases for graphing by `graphs1090.sh`.

Based on naming conventions and standard practice, it likely:
- Uses `psutil` or `/proc` to fetch data
- Sends output via `collectd` or directly to `.rrd` files
- Complements `cpu_graph`, `memory_graph`, etc., in `graphs1090.sh`

---

### 🕓 How to Check When It Was Created

Run:
```bash
stat /usr/share/graphs1090/system_stats.py

