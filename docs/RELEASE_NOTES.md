# Wearable Sweat Sensor v1.1.0 - Laboratory Preview

Windows x64 portable software for BLE discovery and multi-sensor voltage acquisition, with the English quick-start manual included.

## Download

- **Run the software:** `WearableSweatSensor-1.1.0-win64-with-manual.zip`
- **Read the manual:** `WearableSweatSensor-v1.1-Quickstart-EN.pdf`
- **Edit the manual:** `WearableSweatSensor-v1.1-Manual-LaTeX-EN.zip`
- **Verify the portable ZIP:** `WearableSweatSensor-1.1.0-win64-with-manual.zip.sha256`
- **Review simulated checks:** `VALIDATION_SUMMARY.json`

Extract the entire software ZIP and double-click `WearableSweatSensor.exe`. Keep `_internal/` beside the executable. No Python installation is needed. GitHub's automatically generated source archives contain source only, not a ready-to-run application.

## Included functionality

- Automatic BLE scanning, manual MAC entry, and persistent named devices.
- Independent live voltage plots and output files for multiple devices.
- Four event flags with keyboard shortcuts.
- Automatic matching CSV/TXT output and direct access to the data folder.
- Demo streams and opt-in simulated self-tests without physical hardware.
- Illustrated English instructions, including 150 ppm low-point and 2000 ppm high-point standard solutions and waiting for stable curves.

Fresh public installations start with an empty saved-device list. Existing configurations and historical data remain available on upgrade. This distribution keeps application version 1.1.0; changes from the earlier local build concern public defaults, self-test fixtures, documentation, and packaging.

## Validation and limits

See `VALIDATION_SUMMARY.json` for results from this package's unit tests, source GUI acceptance, and frozen executable acceptance. The frozen application is tested with an empty PATH and without inherited Python environment settings.

**Hardware validation is pending.** BLE compatibility and voltage scaling must be confirmed with the actual sensor. This release is not code-signed, and the dependency environment has not been tested on a clean Windows installation. Suggested GitHub status: **pre-release**.
