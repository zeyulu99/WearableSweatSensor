# Changelog

## 1.1.0 - 2026-10-10

- Discover BLE devices in the background with names, MAC addresses, RSSI, and saved/possible-sensor hints.
- Deduplicate advertisements, update names and signal values, and retain unnamed devices.
- Add selected scan results with an editable device name; keep manual MAC entry.
- Stop, retry, and close scans while other sensors continue collecting.
- Preserve multiple independent voltage streams, colored event flags, and automatic CSV/TXT saving.
- Include an English two-page quick-start guide with annotated screenshots and the 150 ppm / 2000 ppm standard-solution procedure.

### GitHub distribution preparation

- Start fresh installations with an empty device list; no laboratory hardware MAC addresses are distributed.
- Preserve existing user configuration and data on upgrade.
- Make GUI acceptance checks work with the bundled device defaults, including an empty list.
- Provide a standalone repository, Windows build workflow, portable package with manual, dependency notices, and checksums.
- Remove build scripts' dependency on the original local backup or host-specific paths.

This is a laboratory preview validated with simulated devices. Physical BLE hardware, voltage scaling, and clean Windows installation testing remain pending.
