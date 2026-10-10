# Wearable Sweat Sensor 1.1.0 - Windows x64

## Start

Extract the entire ZIP, then double-click `WearableSweatSensor.exe`. Keep `_internal/` beside it. Python is included. Read the included `Quickstart-EN.pdf` for illustrated instructions.

## Connect and record

1. Charge and power on the sensor; enable the computer's Bluetooth.
2. Click **Scan Devices**, verify the device MAC, then **Add Selected**.
3. Name the sensor and **Save**. In the main list, select it and **Connect**.
4. Repeat for more devices. Each window has its own plot and output files.
5. Click **Stop**, wait for **Disconnected** and **Saved**, then **Close**.

Use **Add New** for a known MAC. **Possible sensor** is only a broadcast hint; confirm identity by powering one sensor off/on and rescanning. Compatibility also requires the expected sensor BLE protocol.

Fresh public installations have no preloaded device addresses. Previously saved devices and data remain available on upgrade. **Open Demo** produces clearly labeled simulated data for practice without hardware.

## Standard solutions

- Apply **150 ppm** standard solution to the sensing membrane and wait for a stable curve.
- Apply **2000 ppm** standard solution to the sensing membrane and wait for a stable curve.
- After calibration, begin sweat measurement according to the laboratory procedure.

Record the stable voltage at each point. Follow your laboratory SOP for volume, handling between solutions, and stability criteria. The software saves voltage in mV; it does not automatically calibrate or convert to concentration.

## Files and flags

Recording automatically creates matching CSV/TXT files. Click **Open Data Folder** to find them. The default location is `%LOCALAPPDATA%\WearableSweatSensor\Data\wearable device\`.

Columns: `time_s,voltage_mv,event_flag`. Use the colored buttons or keys **1-4** for blue/yellow/red/green events. Event rows repeat the latest time and voltage; exclude these rows from sample statistics. Colors have user-defined meanings. The most recent flag appears in **Last flag**, without an event line on the plot.

Reconnect to start a new file. A failed connection can leave a header-only file; check **Samples**. Closing the main window stops all streams and saves their data. Maximize live windows if the bottom buttons are clipped.

## Validation and diagnostics

This is an unsigned **laboratory preview**, tested using simulated scanning/acquisition. Actual hardware compatibility, voltage scaling, and clean Windows installation validation are pending. See `VALIDATION_SUMMARY.json` and `SIMULATION_TEST_REPORT.json`.

Logs: `%LOCALAPPDATA%\WearableSweatSensor\diagnostics\application.log`.

Optional advanced self-test: `WearableSweatSensor.exe --self-test`. This uses temporary simulated devices and does not access real Bluetooth hardware or modify saved devices/measurement data. Report: `diagnostics\self-test.json`.

`SHA256SUMS.json` lists checksums for all other package files. Third-party notices and licenses are included. See `LICENSE_STATUS.md` for project license status.
