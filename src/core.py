"""Release-only storage and acquisition. The original project is never written."""
import asyncio
import csv
import datetime as dt
import logging
import math
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading
import time
import uuid
import zlib
from collections import deque
from dataclasses import dataclass
from typing import Optional

from bleak import BleakClient

VERSION = "1.1.0"
WRITE_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef0"
NOTIFY_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef2"
MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
LOGGER = logging.getLogger("sweat_sensor")


@dataclass(frozen=True)
class AppPaths:
    home: Path
    device_csv: Path
    log_dir: Path
    diagnostics_dir: Path
    default_devices: Path

    @classmethod
    def resolve(cls):
        override = os.environ.get("SWEAT_SENSOR_HOME")
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        home = Path(override).expanduser().resolve() if override else base / "WearableSweatSensor"
        resource_root = (Path(sys._MEIPASS) if getattr(sys, "frozen", False)
                         else Path(__file__).resolve().parent.parent)
        return cls(home, home / "saved_devices.csv", home / "Data" / "wearable device",
                   home / "diagnostics", resource_root / "resources" / "default_devices.csv")

    def prepare(self):
        for path in (self.home, self.log_dir, self.diagnostics_dir):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class DeviceEntry:
    name: str
    mac_address: str


class DeviceStore:
    def __init__(self, csv_path: Path, legacy_path: Optional[Path] = None,
                 default_path: Optional[Path] = None):
        self.csv_path = Path(csv_path)
        self.legacy_path = legacy_path
        self.default_path = default_path
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate(entry):
        if not entry.name.strip() or not MAC_PATTERN.fullmatch(entry.mac_address):
            raise ValueError("Invalid sensor entry: provide a name and XX:XX:XX:XX:XX:XX MAC address.")

    def _read(self, path):
        entries = []
        seen = set()
        with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not {"name", "mac_address"}.issubset(reader.fieldnames or []):
                raise ValueError(f"Invalid device file: {path}. Expected name,mac_address columns.")
            for row in reader:
                entry = DeviceEntry((row.get("name") or "").strip(),
                                    (row.get("mac_address") or "").strip().upper())
                self._validate(entry)
                if entry.mac_address not in seen:
                    entries.append(entry)
                    seen.add(entry.mac_address)
        return entries

    def load(self):
        if self.csv_path.exists():
            return self._read(self.csv_path)
        entries = []
        if self.legacy_path and self.legacy_path.exists():
            for line in self.legacy_path.read_text(encoding="utf-8").splitlines():
                if "MAC:" in line:
                    name, mac = line.split("MAC:", 1)
                    entry = DeviceEntry(name.strip().rstrip(":"), mac.strip().upper())
                    if entry.name and MAC_PATTERN.fullmatch(entry.mac_address):
                        entries.append(entry)
        if not entries and self.default_path and self.default_path.exists():
            entries = self._read(self.default_path)
        self.save(entries)
        return entries

    def save(self, entries):
        for entry in entries:
            self._validate(entry)
        fd, name = tempfile.mkstemp(prefix="devices_", suffix=".tmp", dir=self.csv_path.parent)
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["name", "mac_address"])
                writer.writerows((entry.name, entry.mac_address.upper()) for entry in entries)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.csv_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def upsert(self, entry):
        entry = DeviceEntry(entry.name.strip(), entry.mac_address.strip().upper())
        self._validate(entry)
        entries = [item for item in self.load() if item.mac_address.upper() != entry.mac_address]
        entries.append(entry)
        entries.sort(key=lambda item: item.name.casefold())
        self.save(entries)
        return entries


class _StopRequested(Exception):
    pass


class BleStreamWorker:
    def __init__(self, device, output_dir, *, client_factory=None, operation_timeout=20.0):
        self.device = device
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.message_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.disconnected_event = threading.Event()
        self.file_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run_thread, name=f"sensor-{device.name}", daemon=False)
        self.start_time = None
        self.records = deque(maxlen=3000)
        self.sample_count = 0
        self.discarded_samples = 0
        self.error_message = None
        self._callback_error = None
        self.client_factory = client_factory or BleakClient
        self.operation_timeout = operation_timeout
        timestamp = dt.datetime.now().strftime("%Y_%m_%d_%H_%M_%S_%f")
        safe_name = self._sanitize_name(device.name)
        self.file_stem = f"{safe_name}_{timestamp}_{uuid.uuid4().hex[:12]}"
        self.txt_path = self.output_dir / f"{self.file_stem}.txt"
        self.csv_path = self.output_dir / f"{self.file_stem}.csv"

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _run_thread(self):
        outcome = "Disconnected"
        try:
            if not self.stop_event.is_set():
                self._write_headers()
                asyncio.run(self._stream())
                if self._callback_error:
                    raise self._callback_error
        except _StopRequested:
            if self._callback_error:
                self.error_message = str(self._callback_error)
                outcome = "Error - recorded data retained"
                LOGGER.error("Acquisition save failed for %s: %s", self.device.name, self._callback_error)
                self.message_queue.put({"type": "error", "message": self.error_message})
            else:
                outcome = "Stopped"
        except Exception as exc:
            self.error_message = str(exc) or type(exc).__name__
            outcome = "Error - recorded data retained"
            LOGGER.exception("Acquisition failed for %s (%s)", self.device.name, self.device.mac_address)
            self.message_queue.put({"type": "error", "message": self.error_message})
        finally:
            self.message_queue.put({"type": "finished", "message": outcome,
                                    "txt_path": str(self.txt_path), "csv_path": str(self.csv_path),
                                    "failed": self.error_message is not None,
                                    "sample_count": self.sample_count})

    async def _operation(self, awaitable):
        task = asyncio.ensure_future(awaitable)
        deadline = time.monotonic() + self.operation_timeout
        try:
            while True:
                if self.stop_event.is_set():
                    raise _StopRequested()
                if self.disconnected_event.is_set():
                    raise ConnectionError("Sensor disconnected. Existing samples are saved; reconnect to start a new file.")
                done, _ = await asyncio.wait({task}, timeout=0.05)
                if task in done:
                    return task.result()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Bluetooth operation timed out. Check sensor power and Bluetooth, then reconnect.")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _on_disconnect(self, _client):
        self.disconnected_event.set()

    async def _stream(self):
        self.message_queue.put({"type": "status", "message": "Connecting..."})
        client = self.client_factory(self.device.mac_address, disconnected_callback=self._on_disconnect,
                                     timeout=self.operation_timeout)
        subscribed = False
        start_attempted = False
        try:
            await self._operation(client.connect())
            if not client.is_connected:
                raise ConnectionError("Could not connect. Check sensor power, MAC address and Bluetooth.")
            await self._operation(client.start_notify(NOTIFY_CHARACTERISTIC_UUID, self._notification_handler))
            subscribed = True
            start_attempted = True
            # Preserve the original 0x01/0x00 protocol and signed little-endian voltage scaling.
            await self._operation(client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, bytearray([1]), response=True))
            self.message_queue.put({"type": "status", "message": "Connected - waiting for data"})
            while not self.stop_event.is_set():
                if self.disconnected_event.is_set() or not client.is_connected:
                    raise ConnectionError("Sensor disconnected. Existing samples are saved; reconnect to start a new file.")
                await asyncio.sleep(0.05)
            if self._callback_error:
                raise self._callback_error
        finally:
            # Cleanup is bounded and attempted independently even if an earlier cleanup step fails.
            if client.is_connected and start_attempted:
                try:
                    await asyncio.wait_for(client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, bytearray([0]), response=True), 5)
                except Exception:
                    LOGGER.warning("Could not send stop command to %s", self.device.name, exc_info=True)
            if client.is_connected and subscribed:
                try:
                    await asyncio.wait_for(client.stop_notify(NOTIFY_CHARACTERISTIC_UUID), 5)
                except Exception:
                    LOGGER.warning("Could not stop notifications for %s", self.device.name, exc_info=True)
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except Exception:
                LOGGER.warning("Could not disconnect %s", self.device.name, exc_info=True)

    def _notification_handler(self, _sender, data):
        if self.stop_event.is_set():
            return
        # The existing firmware variants use 16- or 32-bit signed integer payloads.
        # Their real byte width still needs hardware acceptance; never accept arbitrary lengths.
        if len(data) not in (2, 4):
            self.discarded_samples += 1
            LOGGER.warning("Invalid payload length %s from %s", len(data), self.device.name)
            self.message_queue.put({"type": "warning", "message": "Invalid Bluetooth sample ignored"})
            return
        try:
            now = time.perf_counter()
            if self.start_time is None:
                self.start_time = now
            elapsed = round(now - self.start_time, 2)
            value = round(0.1 * int.from_bytes(data, "little", signed=True), 1)
            self._record_sample(elapsed, value)
        except Exception as exc:
            self._callback_error = exc
            self.stop_event.set()

    def _record_sample(self, elapsed, value):
        self._append_record(elapsed, value, "")
        self.records.append((elapsed, value))
        self.sample_count += 1
        self.message_queue.put({"type": "data", "time_s": elapsed, "value_mv": value})

    def add_flag(self, flag_name, elapsed_s, value_mv):
        if flag_name not in {"blue", "yellow", "red", "green"}:
            raise ValueError("Unknown event flag")
        if self.stop_event.is_set() or not self.thread.is_alive():
            return
        self._append_record(elapsed_s, value_mv, flag_name)
        self.message_queue.put({"type": "flag", "flag_name": flag_name,
                                "time_s": elapsed_s, "value_mv": value_mv})

    def _write_headers(self):
        # Exclusive creation prevents even an unexpected session ID collision from overwriting data.
        for path in (self.csv_path, self.txt_path):
            with path.open("x", encoding="utf-8", newline="") as handle:
                handle.write("time_s,voltage_mv,event_flag\n")
                handle.flush()
                os.fsync(handle.fileno())

    def _append_record(self, elapsed_s, value_mv, event_flag):
        line = f"{elapsed_s:.2f},{value_mv:.1f},{event_flag}\n"
        with self.file_lock:
            for path in (self.csv_path, self.txt_path):
                with path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())

    @staticmethod
    def _sanitize_name(name):
        cleaned = re.sub(r"[^\w-]+", "_", name.strip(), flags=re.UNICODE)
        return (cleaned.strip("_") or "sensor")[:60]


class FakeBleStreamWorker(BleStreamWorker):
    def __init__(self, device, output_dir, *, sample_interval=2.0, fail_after=None):
        super().__init__(device, output_dir)
        self.sample_interval = sample_interval
        self.fail_after = fail_after

    async def _stream(self):
        self.message_queue.put({"type": "status", "message": "DEMO - simulated voltage, no Bluetooth"})
        offset = zlib.crc32(self.device.mac_address.encode()) % 70
        index = 0
        while not self.stop_event.is_set():
            if self.fail_after is not None and index >= self.fail_after:
                raise ConnectionError("Simulated sensor disconnected")
            elapsed = round(index * self.sample_interval, 2)
            value = round(125 + offset + 22 * math.sin(elapsed / 18 + offset / 30)
                          + 7 * math.sin(elapsed / 4.5) + ((index * 17) % 9 - 4) * 1.3, 1)
            self._record_sample(elapsed, value)
            index += 1
            deadline = time.monotonic() + self.sample_interval
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                await asyncio.sleep(min(0.05, self.sample_interval))
