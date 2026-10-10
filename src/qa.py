"""Opt-in acceptance check using the actual Tk GUI and simulated workers only."""
import csv
import json
import logging
from pathlib import Path
import tempfile
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch


def run_gui_acceptance(report_path):
    from app import AddDeviceDialog, SensorApp
    from core import AppPaths, DeviceEntry, DeviceStore, FakeBleStreamWorker, VERSION
    from scanning import BleScanWorker, SimulatedScanner
    report_path = Path(report_path).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results = []
    root = None
    app = None
    errors = []

    def record(name, condition):
        if not condition:
            raise AssertionError(name)
        results.append({"check": name, "passed": True})

    def pump_until(predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("Timed out waiting for GUI condition")

    with tempfile.TemporaryDirectory(prefix="sweat_gui_验收_") as temp:
        home = Path(temp)
        default = AppPaths.resolve().default_devices
        paths = AppPaths(home, home/"saved_devices.csv", home/"Data"/"wearable device",
                         home/"diagnostics", default)
        try:
            # Import the actual Windows backend without scanning or connecting to any radio.
            import bleak.backends.winrt.client
            import bleak.backends.winrt.scanner
            record("Windows BLE backend imports without external Python", True)
            root = tk.Tk()
            root.withdraw()
            root.report_callback_exception = lambda *exc: errors.append(str(exc[1]))
            ttk.Style(root).theme_use("clam")
            with patch("app.messagebox.showerror", side_effect=lambda title, msg, **kw: errors.append(str(msg))), \
                 patch("app.messagebox.showwarning") as warning:
                app = SensorApp(root, paths)
                expected_defaults = DeviceStore(home/"expected-defaults.csv", default_path=default).load()
                record("bundled device defaults load without adding private addresses", app.devices == expected_defaults)
                app.connect_selected()
                record("Connect without selection shows validation", warning.called)

                dialog = AddDeviceDialog(root, app._save_device)
                dialog.dialog.withdraw()
                dialog.name_entry.insert(0, "模拟测试新设备")
                dialog.mac_entry.insert(0, "AA:BB:CC:DD:EE:99")
                dialog._save()
                record("Add New dialog saves a new sensor", len(app.devices) == len(expected_defaults) + 1)
                record("new device survives reload", any(d.name == "模拟测试新设备" for d in app.store.load()))

                # Exercise the actual Open Demo button handler, including unique identities.
                demos = [app.open_demo_stream() for _ in range(3)]
                for window in demos:
                    window.window.withdraw()
                pump_until(lambda: all(w.sample_count >= 2 for w in demos), timeout=10)
                record("three simultaneous GUI demo streams display samples", all(w.last_sample is not None for w in demos))
                record("three streams have different file names", len({w.worker.csv_path for w in demos}) == 3)
                record("demo devices have independent voltage traces", len({w.last_sample[1] for w in demos}) > 1)
                record("voltage labels and sample counters are updated", all("mV" in w.last_value_var.get()
                       and w.samples_var.get() == f"Samples: {w.sample_count}" for w in demos))
                record("GUI curves contain displayed voltage samples", all(len(w.line.get_xdata()) >= 2 for w in demos))
                duplicate = app.open_stream(demos[0].device)
                record("duplicate active connection reuses the window", duplicate is demos[0] and len(app.stream_windows) == 3)

                for color in ("blue", "yellow", "red", "green"):
                    demos[0].mark_flag(color)
                pump_until(lambda: "green" in demos[0].flag_var.get())
                with demos[0].worker.csv_path.open(encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
                record("all four event flags persist to CSV", {r["event_flag"] for r in rows if r["event_flag"]}
                       == {"blue", "yellow", "red", "green"})

                # Force actual geometry calculations and verify controls fit in their windows.
                demos[0].window.deiconify()
                root.update()
                record("Stop and Close controls fit within the acquisition window",
                       all(b.winfo_rooty() + b.winfo_height() <= demos[0].window.winfo_rooty()
                           + demos[0].window.winfo_height() for b in (demos[0].stop_button, demos[0].close_button)))
                demos[0].figure.savefig(report_path.with_suffix(".png"), dpi=100)
                demos[0].window.withdraw()

                demos[0].stop_stream()
                pump_until(lambda: demos[0].is_finished and not demos[0].worker.thread.is_alive())
                before = [w.sample_count for w in demos[1:]]
                pump_until(lambda: all(w.sample_count > n for w,n in zip(demos[1:],before)), timeout=6)
                record("stopping one GUI stream leaves two collecting", all(w.worker.thread.is_alive() for w in demos[1:]))
                record("stopped stream shows saved output", demos[0].file_var.get().startswith("Saved:"))

                closed = demos[1]
                closed.close()
                pump_until(lambda: not closed.worker.thread.is_alive() and not closed.window.winfo_exists())
                record("closing a live stream waits for save and exits", closed.worker.csv_path.exists())

                device = DeviceEntry("Failure simulation", "FAKE:FAILURE")
                worker = FakeBleStreamWorker(device, paths.log_dir, sample_interval=0.05, fail_after=3)
                failed = app.open_stream(device, worker=worker)
                failed.window.withdraw()
                pump_until(lambda: failed.is_finished and not worker.thread.is_alive())
                record("simulated failure reaches GUI error state", worker.error_message is not None
                       and "Error" in failed.status_var.get())
                record("failed stream does not stop healthy stream", demos[2].worker.thread.is_alive())
                record("expected failure is surfaced to the user", len(errors) == 1 and "Simulated" in errors[0])
                errors.clear()

                with patch("app.os.startfile") as open_folder:
                    app.open_data_folder()
                    record("Open Data Folder targets the user data directory", open_folder.call_args.args[0] == str(paths.log_dir))

                def scan_factory(**options):
                    return lambda: BleScanWorker(scanner_factory=lambda **kw: SimulatedScanner(**kw, **options), duration=0.2)

                scan = app.open_scan_dialog(worker_factory=scan_factory())
                scan.dialog.withdraw()
                pump_until(lambda: not scan.scanning and not scan.worker.thread.is_alive())
                record("Scan Devices deduplicates repeated advertisements in GUI", len(scan.tree.get_children()) == 4)
                record("scan updates advertised name and RSSI", scan.tree.item("AA:BB:CC:DD:EE:50", "values")[:3]
                       == ("nRF52840_Temperature", "AA:BB:CC:DD:EE:50", "-48"))
                record("unnamed BLE devices remain visible", scan.tree.item("AA:BB:CC:DD:EE:52", "values")[0] == "(Unnamed device)")
                root.deiconify()
                scan.dialog.deiconify()
                root.update()
                record("scan controls fit within the visible scan window", all(
                    button.winfo_ismapped() and button.winfo_rooty() + button.winfo_height()
                    <= scan.dialog.winfo_rooty() + scan.dialog.winfo_height()
                    for button in (scan.scan_button, scan.stop_button, scan.add_button, scan.close_button)))
                root.withdraw()
                scan.dialog.withdraw()
                record("repeated Scan Devices reuses existing dialog", app.open_scan_dialog() is scan)
                scan.dialog.withdraw()
                scan.only_sensors.set(True)
                scan._render()
                record("optional sensor filter hides unrelated advertisements", len(scan.tree.get_children()) == 3)
                scan.only_sensors.set(False)
                scan._render()
                scan.tree.selection_set("AA:BB:CC:DD:EE:50")
                add = scan.select_device()
                add.dialog.withdraw()
                record("Add Selected prefills device MAC automatically", add.mac_entry.get() == "AA:BB:CC:DD:EE:50")
                add.name_entry.delete(0, "end")
                add.name_entry.insert(0, "扫描添加的传感器")
                add._save()
                record("scanned device is saved and survives reload", any(d.name == "扫描添加的传感器"
                       and d.mac_address == "AA:BB:CC:DD:EE:50" for d in app.store.load()))
                record("saved scan result is marked as saved", scan.tree.item("AA:BB:CC:DD:EE:50", "values")[3] == "Saved device")
                add = scan.select_device()
                add.dialog.withdraw()
                record("readding saved MAC preserves existing custom name", add.name_entry.get() == "扫描添加的传感器")
                add._save()
                record("readding scanned MAC does not duplicate configuration", len([d for d in app.store.load()
                       if d.mac_address == "AA:BB:CC:DD:EE:50"]) == 1)

                sample_before = demos[2].sample_count
                scan.worker_factory = scan_factory(advertisements=[])
                scan.start_scan()
                pump_until(lambda: not scan.scanning and not scan.worker.thread.is_alive())
                record("empty rescan clears previous results and explains next step", not scan.tree.get_children()
                       and "No BLE devices" in scan.status_var.get())
                record("Add Selected is disabled without a result", str(scan.add_button.cget("state")) == "disabled")
                scan.worker_factory = scan_factory(start_error="Simulated Bluetooth disabled")
                scan.start_scan()
                pump_until(lambda: not scan.scanning and not scan.worker.thread.is_alive())
                record("scanner error appears in GUI without blocking other windows", "Scan failed" in scan.status_var.get()
                       and "Simulated Bluetooth disabled" in scan.status_var.get())
                record("retry is enabled after scanner failure", str(scan.scan_button.cget("state")) == "normal")
                scan.worker_factory = scan_factory()
                scan.start_scan()
                pump_until(lambda: not scan.scanning and not scan.worker.thread.is_alive())
                record("rescan succeeds after previous failure", len(scan.tree.get_children()) == 4)
                pump_until(lambda: demos[2].sample_count > sample_before, timeout=5)
                record("existing voltage acquisition continues during scans", demos[2].worker.thread.is_alive())

                scan.worker_factory = lambda: BleScanWorker(scanner_factory=SimulatedScanner, duration=60)
                scan.start_scan()
                pump_until(lambda: bool(scan.results))
                scan.stop_scan()
                pump_until(lambda: not scan.scanning and not scan.worker.thread.is_alive())
                record("Stop Scan cancels and cleans scanner", "Scan stopped" in scan.status_var.get())
                scan.start_scan()
                scan.close()
                pump_until(lambda: not scan.worker.thread.is_alive() and not scan.dialog.winfo_exists())
                record("closing active scan waits for background cleanup", not scan.worker.thread.is_alive())

                slow_factory = lambda: BleScanWorker(scanner_factory=lambda **kw: SimulatedScanner(**kw, start_delay=60), duration=60)
                closing_scan = app.open_scan_dialog(worker_factory=slow_factory)
                closing_scan.dialog.withdraw()

                app.close()
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    try:
                        root.update()
                        if not root.winfo_exists():
                            break
                    except tk.TclError:
                        break
                    time.sleep(0.01)
                record("main-window exit stops all acquisition threads", all(not w.worker.thread.is_alive()
                       for w in [*demos, failed]))
                try:
                    exists = root.winfo_exists()
                except tk.TclError:
                    exists = False
                record("main window is destroyed after workers finish", not exists)
                record("main-window exit cancels a scanner still starting", not closing_scan.worker.thread.is_alive())
                for window in [*demos, failed]:
                    record(f"TXT and CSV match for {window.device.name}",
                           window.worker.csv_path.read_bytes() == window.worker.txt_path.read_bytes())
                record("no unexpected Tk callback errors", not errors)
        except Exception as exc:
            logging.getLogger("sweat_sensor").exception("GUI acceptance failed")
            results.append({"check": str(exc), "passed": False})
        finally:
            if app:
                for dialog in app.scan_dialogs:
                    if dialog.worker:
                        dialog.worker.stop()
                        dialog.worker.thread.join(5)
                for window in app.stream_windows:
                    window.worker.stop()
                for window in app.stream_windows:
                    window.worker.thread.join(5)
            if root:
                try:
                    root.destroy()
                except tk.TclError:
                    pass
        report = {"version": VERSION, "passed": bool(results) and all(r["passed"] for r in results),
                  "mode": "simulated GUI; no Bluetooth radio or hardware used", "checks": results}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
