"""Live discovery view; all Tk updates stay on the main thread."""
import queue
import tkinter as tk
from tkinter import ttk

from core import DeviceEntry
from scanning import BleScanWorker


class ScanDialog:
    def __init__(self, parent, on_select, saved_devices, *, worker_factory=None, duration=8.0):
        self.on_select = on_select
        self.saved_devices = saved_devices
        self.worker_factory = worker_factory or (lambda: BleScanWorker(duration=duration))
        self.worker = None
        self.results = {}
        self.last_error = None
        self.scanning = False
        self.close_pending = False
        self._poll_id = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Scan Nearby BLE Devices")
        self.dialog.geometry("850x460")
        self.dialog.minsize(750, 400)
        self.dialog.transient(parent)
        self.dialog.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self.dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Nearby Bluetooth Low Energy Devices", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Power on your sensor. Select a device, then name and save it before connecting.").pack(anchor="w", pady=(4, 8))
        self.only_sensors = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Show possible sensors and saved devices only",
                        variable=self.only_sensors, command=self._render).pack(anchor="w", pady=(0, 8))
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table_frame, columns=("name", "mac", "rssi", "hint"), show="headings", selectmode="browse")
        for column, title, width in (("name", "Device Name", 250), ("mac", "MAC Address", 170),
                                      ("rssi", "Signal (dBm)", 95), ("hint", "Hint", 160)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=70, anchor="w")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_buttons())
        self.tree.bind("<Double-1>", lambda _event: self.select_device())
        self.status_var = tk.StringVar(value="Ready to scan")
        ttk.Label(frame, textvariable=self.status_var, wraplength=790, foreground="#1f5d99").pack(anchor="w", pady=(8, 2))
        ttk.Label(frame, text="'Possible sensor' is a hint, not a compatibility check. Unnamed devices are also shown.",
                  wraplength=790).pack(anchor="w", pady=(0, 8))
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.scan_button = ttk.Button(buttons, text="Scan Again", command=self.start_scan)
        self.scan_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="Stop Scan", command=self.stop_scan)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.close_button = ttk.Button(buttons, text="Close", command=self.close)
        self.close_button.pack(side="right")
        self.add_button = ttk.Button(buttons, text="Add Selected", command=self.select_device)
        self.add_button.pack(side="right", padx=(0, 8))
        self.start_scan()

    def _saved(self):
        return {d.mac_address.upper(): d for d in self.saved_devices()}

    def start_scan(self):
        if self.close_pending or (self.worker and self.worker.thread.is_alive()):
            return
        self.results.clear()
        self.last_error = None
        self.scanning = True
        self.status_var.set("Scanning nearby BLE devices...")
        self._render()
        self.worker = self.worker_factory()
        self.worker.start()
        self._update_buttons()
        if self._poll_id:
            self.dialog.after_cancel(self._poll_id)
        self._poll_id = self.dialog.after(100, self._poll)

    def stop_scan(self):
        if self.worker and self.worker.thread.is_alive():
            self.worker.stop()
            self.status_var.set("Stopping scan...")
            self.stop_button.config(state="disabled")

    def _poll(self):
        for _ in range(100):
            try:
                message = self.worker.message_queue.get_nowait()
            except queue.Empty:
                break
            if message["type"] == "results":
                self.results = {d.mac_address: d for d in message["devices"]}
                self._render()
                if self.scanning and not self.worker.stop_event.is_set():
                    self.status_var.set(f"Scanning... {len(self.results)} device(s) found")
            elif message["type"] == "error":
                self.last_error = message["message"]
            elif message["type"] == "finished":
                self.scanning = False
                if message["failed"]:
                    self.status_var.set("Scan failed. Turn on Bluetooth and try again. Details: " + (self.last_error or "Unknown error"))
                elif message["cancelled"]:
                    self.status_var.set(f"Scan stopped: {len(self.results)} device(s) found")
                elif not self.results:
                    self.status_var.set("No BLE devices found. Check sensor power, move closer and scan again.")
                else:
                    self.status_var.set(f"Scan complete: {len(self.results)} device(s) found. Select one to add.")
                self._update_buttons()
        # The finished message can be read just before the background thread returns.
        self._update_buttons()
        if self.close_pending and not self.worker.thread.is_alive():
            self._destroy()
            return
        self._poll_id = self.dialog.after(100, self._poll)

    def _render(self):
        selected = self.tree.selection()
        saved = self._saved()
        shown = [d for d in self.results.values()
                 if not self.only_sensors.get() or d.possible_sensor or d.mac_address in saved]
        shown.sort(key=lambda d: (d.mac_address not in saved, not d.possible_sensor,
                                  -(d.rssi if d.rssi is not None else -999), d.mac_address))
        # Update existing rows in place to avoid selection loss and flicker during advertisements.
        addresses = {d.mac_address for d in shown}
        for iid in self.tree.get_children():
            if iid not in addresses:
                self.tree.delete(iid)
        for index, result in enumerate(shown):
            hint = "Saved device" if result.mac_address in saved else ("Possible sensor" if result.possible_sensor else "BLE device")
            values = (result.name or "(Unnamed device)", result.mac_address,
                      result.rssi if result.rssi is not None else "--", hint)
            if self.tree.exists(result.mac_address):
                self.tree.item(result.mac_address, values=values)
                self.tree.move(result.mac_address, "", index)
            else:
                self.tree.insert("", index, iid=result.mac_address, values=values)
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._update_buttons()

    def _update_buttons(self):
        active = bool(self.worker and self.worker.thread.is_alive())
        self.scan_button.config(state="disabled" if active or self.close_pending else "normal")
        self.stop_button.config(state="normal" if active and not self.worker.stop_event.is_set() else "disabled")
        self.add_button.config(state="normal" if self.tree.selection() and not self.close_pending else "disabled")

    def select_device(self):
        if self.close_pending:
            return None
        selected = self.tree.selection()
        if not selected or selected[0] not in self.results:
            return None
        result = self.results[selected[0]]
        previous = self._saved().get(result.mac_address)
        name = previous.name if previous else (result.name or "sensor_" + result.mac_address.replace(":", "")[-6:])
        return self.on_select(DeviceEntry(name, result.mac_address))

    def close(self):
        self.close_pending = True
        if self.worker and self.worker.thread.is_alive():
            self.stop_scan()
            self._update_buttons()
            return
        self._destroy()

    def _destroy(self):
        if self._poll_id:
            self.dialog.after_cancel(self._poll_id)
            self._poll_id = None
        self.dialog.destroy()
