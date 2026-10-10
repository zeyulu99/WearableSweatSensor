"""Windows release GUI derived from the original application; original files stay untouched."""
import argparse
from collections import deque
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from core import (AppPaths, BleStreamWorker, DeviceEntry, DeviceStore, FakeBleStreamWorker,
                  MAC_PATTERN, VERSION)
from scan_dialog import ScanDialog

PATHS = AppPaths.resolve()
LOG_DIR = PATHS.log_dir
LOGGER = logging.getLogger("sweat_sensor")


class StreamWindow:
    def __init__(self, parent: tk.Misc, device: DeviceEntry, *, output_dir=None,
                 worker=None, on_change=None) -> None:
        self.device = device
        worker_cls = FakeBleStreamWorker if device.mac_address.upper().startswith("FAKE:") else BleStreamWorker
        self.worker = worker or worker_cls(device=device, output_dir=output_dir or LOG_DIR)
        self.times = deque(maxlen=3000)
        self.values = deque(maxlen=3000)
        self.sample_count = 0
        self.on_change = on_change
        self.close_pending = False
        self._poll_id = None
        self.last_sample: Optional[Tuple[float, float]] = None
        self.is_stopping = False
        self.is_finished = False
        self.is_hidden = False

        self.window = tk.Toplevel(parent)
        mode = "DEMO - " if isinstance(self.worker, FakeBleStreamWorker) else ""
        self.window.title(f"{mode}Live Stream - {device.name}")
        self.window.geometry("900x760")
        self.window.minsize(700, 650)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.status_var = tk.StringVar(value="Waiting to connect...")
        self.last_value_var = tk.StringVar(value="Last value: -- mV")
        self.samples_var = tk.StringVar(value="Samples: 0")
        self.file_var = tk.StringVar(value="Saving to: --")
        self.flag_var = tk.StringVar(value="Last flag: none")

        info_frame = ttk.Frame(self.window, padding=12)
        info_frame.pack(fill="x")
        ttk.Label(info_frame, text=f"Sensor: {device.name}", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(info_frame, text=f"MAC: {device.mac_address}").pack(anchor="w")
        ttk.Label(info_frame, textvariable=self.status_var).pack(anchor="w", pady=(6, 0))
        ttk.Label(info_frame, textvariable=self.last_value_var).pack(anchor="w")
        ttk.Label(info_frame, textvariable=self.samples_var).pack(anchor="w")
        ttk.Label(info_frame, textvariable=self.file_var, foreground="#1f5d99").pack(anchor="w", pady=(2, 0))
        ttk.Label(info_frame, textvariable=self.flag_var).pack(anchor="w")

        self.figure = Figure(figsize=(8, 4.8), dpi=100)
        self.axis = self.figure.add_subplot(111)
        self.axis.set_title("Keys: (1) Blue  (2) Yellow  (3) Red  (4) Green", fontsize=10)
        self.axis.set_xlabel("Time (s)")
        self.axis.set_ylabel("Voltage (mV)")
        self.axis.grid(True, alpha=0.3)
        (self.line,) = self.axis.plot([], [], color="#1f77b4", linewidth=1.8)

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.window)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=12)
        self.figure.tight_layout()

        flag_frame = ttk.LabelFrame(self.window, text="Event Flags", padding=(12, 8))
        flag_frame.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(
            flag_frame, text="Press number keys to add flags: (1) Blue, (2) Yellow, (3) Red, (4) Green"
        ).pack(anchor="w", pady=(0, 8))
        tk.Button(
            flag_frame,
            text="Blue Flag (1)",
            command=lambda: self.mark_flag("blue"),
            bg="#1f77b4",
            fg="white",
            activebackground="#175a86",
            activeforeground="white",
            width=15,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            flag_frame,
            text="Yellow Flag (2)",
            command=lambda: self.mark_flag("yellow"),
            bg="#f2c94c",
            fg="black",
            activebackground="#c7a53c",
            activeforeground="black",
            width=15,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            flag_frame,
            text="Red Flag (3)",
            command=lambda: self.mark_flag("red"),
            bg="#d64545",
            fg="white",
            activebackground="#ab3434",
            activeforeground="white",
            width=15,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            flag_frame,
            text="Green Flag (4)",
            command=lambda: self.mark_flag("green"),
            bg="#2ca25f",
            fg="white",
            activebackground="#22824c",
            activeforeground="white",
            width=15,
        ).pack(side="left")

        button_frame = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        button_frame.pack(fill="x")
        self.close_button = ttk.Button(button_frame, text="Close", command=self.close, state="disabled")
        self.close_button.pack(side="right")
        self.stop_button = ttk.Button(button_frame, text="Stop", command=self.stop_stream)
        self.stop_button.pack(side="right", padx=(0, 8))

        self.window.bind("<Key-1>", lambda event: self.mark_flag("blue"))
        self.window.bind("<Key-2>", lambda event: self.mark_flag("yellow"))
        self.window.bind("<Key-3>", lambda event: self.mark_flag("red"))
        self.window.bind("<Key-4>", lambda event: self.mark_flag("green"))

        self.file_var.set(f"Saving to: {self.worker.csv_path.name}")
        self.worker.start()
        self._poll_id = self.window.after(200, self._poll_messages)

    def _poll_messages(self) -> None:
        for _ in range(200):
            try:
                message = self.worker.message_queue.get_nowait()
            except queue.Empty:
                break

            message_type = message["type"]
            if message_type == "status":
                self.status_var.set(message["message"])
            elif message_type == "data":
                self._handle_data(message["time_s"], message["value_mv"])
                if not self.is_stopping:
                    self.status_var.set("DEMO - simulated data" if isinstance(self.worker, FakeBleStreamWorker)
                                        else "Collecting voltage")
            elif message_type == "warning":
                self.status_var.set(message["message"])
            elif message_type == "finished":
                self.is_finished = True
                self.status_var.set(message["message"])
                label = "Saved" if Path(message['csv_path']).exists() else "No data file created"
                self.file_var.set(f"{label}: {Path(message['csv_path']).name}")
                self.stop_button.config(state="disabled")
                self.close_button.config(state="normal")
                if self.on_change:
                    self.on_change()
                if self.close_pending:
                    self._destroy()
                    return
            elif message_type == "flag":
                self.flag_var.set(
                    f"Last flag: {message['flag_name']} at {message['time_s']:.2f} s ({message['value_mv']:.1f} mV)"
                )
            elif message_type == "error":
                self.status_var.set("Error")
                self.stop_button.config(state="disabled")
                self.close_button.config(state="normal")
                if not self.close_pending and not self.is_stopping:
                    messagebox.showerror("BLE Stream Error", message["message"], parent=self.window)

        if self.window.winfo_exists():
            self._poll_id = self.window.after(200, self._poll_messages)

    def _handle_data(self, time_s: float, value_mv: float) -> None:
        self.times.append(time_s)
        self.values.append(value_mv)
        self.sample_count += 1
        self.last_sample = (time_s, value_mv)
        self.last_value_var.set(f"Last value: {value_mv:.1f} mV")
        self.samples_var.set(f"Samples: {self.sample_count}")
        self.line.set_data(list(self.times), list(self.values))
        self.axis.relim()
        self.axis.autoscale_view()
        self.canvas.draw_idle()

    def mark_flag(self, flag_name: str) -> None:
        if self.is_finished or self.is_stopping:
            return
        if self.last_sample is None:
            messagebox.showwarning("No data yet", "Wait for at least one sample before adding a flag.", parent=self.window)
            return
        time_s, value_mv = self.last_sample
        try:
            self.worker.add_flag(flag_name, time_s, value_mv)
        except OSError:
            LOGGER.exception("Event flag could not be saved")
            self.stop_stream()
            messagebox.showerror("Save Error", "Could not save event flag. Check the data folder and available disk space.",
                                 parent=self.window)

    def stop_stream(self) -> None:
        if self.is_stopping or self.is_finished:
            return
        self.is_stopping = True
        self.stop_button.config(state="disabled")
        self.status_var.set("Stopping stream...")
        self.worker.stop()

    def close(self) -> None:
        if self.worker.thread.is_alive():
            self.close_pending = True
            self.stop_stream()
            self.status_var.set("Stopping and saving before closing...")
            return
        self._destroy()

    def _destroy(self):
        if self._poll_id:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.destroy()
        if self.on_change:
            self.on_change()


class AddDeviceDialog:
    def __init__(self, parent: tk.Misc, on_save, initial=None) -> None:
        self.on_save = on_save
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Add New Sensor")
        self.dialog.geometry("390x240")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        frame = ttk.Frame(self.dialog, padding=14)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Sensor name").grid(row=0, column=0, sticky="w")
        self.name_entry = ttk.Entry(frame, width=34)
        self.name_entry.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="MAC address").grid(row=2, column=0, sticky="w")
        self.mac_entry = ttk.Entry(frame, width=34)
        self.mac_entry.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        if initial:
            self.name_entry.insert(0, initial.name)
            self.mac_entry.insert(0, initial.mac_address)

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=4, column=0, sticky="e")
        ttk.Button(button_frame, text="Cancel", command=self.dialog.destroy).pack(side="right")
        ttk.Button(button_frame, text="Save", command=self._save).pack(side="right", padx=(0, 8))

        self.dialog.columnconfigure(0, weight=1)
        self.name_entry.focus_set()

    def _save(self) -> None:
        name = self.name_entry.get().strip()
        mac_address = self.mac_entry.get().strip().upper()

        if not name:
            messagebox.showwarning("Missing name", "Please enter a sensor name.", parent=self.dialog)
            return
        if not MAC_PATTERN.match(mac_address):
            messagebox.showwarning(
                "Invalid MAC",
                "Please enter a valid MAC address in the form XX:XX:XX:XX:XX:XX.",
                parent=self.dialog,
            )
            return

        if self.on_save(DeviceEntry(name=name, mac_address=mac_address)):
            self.dialog.destroy()


class SensorApp:
    def __init__(self, root: tk.Tk, paths=None) -> None:
        self.root = root
        self.paths = paths or PATHS
        self.paths.prepare()
        self.closing = False
        self.demo_counter = 0
        self.root.title(f"Wearable Sweat Sensor v{VERSION}")
        self.root.geometry("740x460")
        self.root.minsize(680, 420)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.store = DeviceStore(csv_path=self.paths.device_csv, default_path=self.paths.default_devices)
        self.devices = self.store.load()
        self.stream_windows = []
        self.scan_dialogs = []

        main_frame = ttk.Frame(root, padding=14)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(
            main_frame,
            text="Saved Sensors",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            main_frame,
            text="Scan nearby sensors or add a MAC address, then select a saved sensor and connect.",
        ).pack(anchor="w", pady=(0, 10))

        self.tree = ttk.Treeview(main_frame, columns=("name", "mac"), show="headings", height=12)
        self.tree.heading("name", text="Sensor Name")
        self.tree.heading("mac", text="MAC Address")
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("mac", width=220, anchor="w")
        self.tree.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(main_frame, textvariable=self.status_var, foreground="#1f5d99").pack(anchor="w", pady=(8, 0))

        button_frame = ttk.Frame(main_frame)
        button_frame.pack(fill="x", pady=(10, 0))
        ttk.Button(button_frame, text="Add New", command=self.open_add_dialog).pack(side="left")
        ttk.Button(button_frame, text="Scan Devices", command=self.open_scan_dialog).pack(side="left", padx=(8, 0))
        ttk.Button(button_frame, text="Open Demo", command=self.open_demo_stream).pack(side="left", padx=(8, 0))
        ttk.Button(button_frame, text="Open Data Folder", command=self.open_data_folder).pack(side="left", padx=(8, 0))
        ttk.Button(button_frame, text="Connect", command=self.connect_selected).pack(side="right")

        self._reload_tree()

    def _reload_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for device in self.devices:
            self.tree.insert("", "end", values=(device.name, device.mac_address))

    def open_add_dialog(self, initial=None):
        if not self.closing:
            return AddDeviceDialog(self.root, self._save_device, initial=initial)

    def open_scan_dialog(self, *, worker_factory=None, duration=8.0):
        if self.closing:
            return None
        self.scan_dialogs = [d for d in self.scan_dialogs if d.dialog.winfo_exists()]
        if self.scan_dialogs:
            dialog = self.scan_dialogs[0]
            dialog.dialog.deiconify()
            dialog.dialog.lift()
            return dialog
        dialog = ScanDialog(self.root, self.open_add_dialog, lambda: self.devices,
                            worker_factory=worker_factory, duration=duration)
        self.scan_dialogs.append(dialog)
        return dialog

    def _save_device(self, entry: DeviceEntry) -> None:
        if self.closing:
            return False
        try:
            self.devices = self.store.upsert(entry)
        except (OSError, ValueError) as exc:
            LOGGER.exception("Device could not be saved")
            messagebox.showerror("Save Error", str(exc), parent=self.root)
            return False
        self._reload_tree()
        self.status_var.set(f"Saved {entry.name} ({entry.mac_address})")
        for dialog in self.scan_dialogs:
            if dialog.dialog.winfo_exists():
                dialog._render()
        return True

    def connect_selected(self) -> None:
        if self.closing:
            return
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("No sensor selected", "Please select a saved sensor first.", parent=self.root)
            return

        item_values = self.tree.item(selection[0], "values")
        device = DeviceEntry(name=item_values[0], mac_address=item_values[1])
        self.open_stream(device)

    def open_stream(self, device, *, worker=None):
        if self.closing:
            return None
        for window in self.stream_windows:
            if (window.device.mac_address.upper() == device.mac_address.upper()
                    and window.worker.thread.is_alive()):
                if window.window.winfo_exists():
                    window.window.deiconify()
                    window.window.lift()
                self.status_var.set(f"{device.name} already has an active connection")
                return window
        try:
            stream_window = StreamWindow(self.root, device, output_dir=self.paths.log_dir,
                                         worker=worker, on_change=self._update_summary)
        except OSError as exc:
            LOGGER.exception("Stream could not be opened")
            messagebox.showerror("Data Folder Error", str(exc), parent=self.root)
            return None
        self.stream_windows.append(stream_window)
        self._update_summary()
        return stream_window

    def open_demo_stream(self) -> None:
        self.demo_counter += 1
        device = DeviceEntry(f"demo sensor {self.demo_counter}", f"FAKE:DEMO:{self.demo_counter:08d}")
        return self.open_stream(device)

    def _update_summary(self):
        if self.closing:
            return
        self.stream_windows = [w for w in self.stream_windows
                               if w.window.winfo_exists() or w.worker.thread.is_alive()]
        active = sum(w.worker.thread.is_alive() for w in self.stream_windows)
        self.status_var.set(f"Active streams: {active}")

    def open_data_folder(self):
        os.startfile(str(self.paths.log_dir))

    def close(self):
        if self.closing:
            return
        self.closing = True
        self.status_var.set("Stopping all sensors and saving data...")
        for dialog in self.scan_dialogs:
            if dialog.dialog.winfo_exists():
                dialog.close()
        for window in self.stream_windows:
            window.close_pending = True
            window.stop_stream()
        self._wait_for_workers()

    def _wait_for_workers(self):
        if (any(w.worker.thread.is_alive() for w in self.stream_windows)
                or any(d.worker and d.worker.thread.is_alive() for d in self.scan_dialogs)):
            self.root.after(100, self._wait_for_workers)
            return
        self.root.destroy()


def configure_logging(paths):
    paths.prepare()
    handler = RotatingFileHandler(paths.diagnostics_dir / "application.log", maxBytes=2_000_000,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
    LOGGER.setLevel(logging.INFO)
    LOGGER.addHandler(handler)
    LOGGER.info("Starting Wearable Sweat Sensor %s; frozen=%s", VERSION, getattr(sys, "frozen", False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true", help="Run simulated GUI acceptance checks without Bluetooth")
    parser.add_argument("--report", type=Path, help="Self-test JSON report path")
    args = parser.parse_args()
    root = None
    try:
        configure_logging(PATHS)
        if args.self_test:
            from qa import run_gui_acceptance
            result = run_gui_acceptance(args.report or PATHS.diagnostics_dir / "self-test.json")
            raise SystemExit(0 if result["passed"] else 1)
        root = tk.Tk()
        ttk.Style(root).theme_use("clam")
        def callback_error(exc_type, exc, tb):
            LOGGER.error("GUI callback failed", exc_info=(exc_type, exc, tb))
            messagebox.showerror("Application Error", f"{exc}\n\nDetails: {PATHS.diagnostics_dir}", parent=root)
        root.report_callback_exception = callback_error
        SensorApp(root)
        root.mainloop()
    except Exception as exc:
        LOGGER.exception("Application startup failed")
        if root is None:
            root = tk.Tk()
            root.withdraw()
        messagebox.showerror("Unable to start", f"{exc}\n\nCheck data-folder permissions and the diagnostics log.", parent=root)
        root.destroy()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
