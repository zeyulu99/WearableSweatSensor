"""Cancellable BLE discovery on a background thread; never connects or saves devices."""
import asyncio
from dataclasses import dataclass
import logging
import queue
import threading
import time
from types import SimpleNamespace

from bleak import BleakScanner
from core import MAC_PATTERN

LOGGER = logging.getLogger("sweat_sensor")
TEMPERATURE_SERVICE = "00001809-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class ScanResult:
    name: str
    mac_address: str
    rssi: int | None
    service_uuids: tuple[str, ...] = ()

    @property
    def possible_sensor(self):
        name = self.name.casefold()
        return ("nrf52840" in name or "sweat" in name
                or TEMPERATURE_SERVICE in self.service_uuids)


class _ScanCancelled(Exception):
    pass


class BleScanWorker:
    def __init__(self, *, scanner_factory=None, duration=8.0, operation_timeout=10.0):
        self.scanner_factory = scanner_factory or BleakScanner
        self.duration = duration
        self.operation_timeout = operation_timeout
        self.stop_event = threading.Event()
        self.message_queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="ble-discovery", daemon=False)
        self.devices = {}
        self.error_message = None
        self._revision = 0

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _detected(self, device, advertisement):
        if self.stop_event.is_set():
            return
        address = str(device.address).strip().upper()
        if not MAC_PATTERN.fullmatch(address):
            LOGGER.warning("Ignoring scan result with unsupported address: %s", address)
            return
        previous = self.devices.get(address)
        name = (advertisement.local_name or device.name or (previous.name if previous else "") or "").strip()
        rssi = getattr(advertisement, "rssi", None)
        if rssi is None and previous:
            rssi = previous.rssi
        services = {str(value).lower() for value in (advertisement.service_uuids or [])}
        if previous:
            services.update(previous.service_uuids)
        result = ScanResult(name, address, rssi, tuple(sorted(services)))
        if previous != result:
            self.devices[address] = result
            self._revision += 1

    def _publish(self):
        results = sorted(self.devices.values(), key=lambda d: (
            not d.possible_sensor, -(d.rssi if d.rssi is not None else -999), d.mac_address))
        self.message_queue.put({"type": "results", "devices": results})

    async def _start_scanner(self, scanner):
        task = asyncio.create_task(scanner.start())
        deadline = time.monotonic() + self.operation_timeout
        try:
            while True:
                if self.stop_event.is_set():
                    raise _ScanCancelled()
                done, _ = await asyncio.wait({task}, timeout=0.05)
                if task in done:
                    return task.result()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Bluetooth scanner did not start in time")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _scan(self):
        if self.stop_event.is_set():
            return
        scanner = self.scanner_factory(detection_callback=self._detected)
        try:
            await self._start_scanner(scanner)
            deadline = time.monotonic() + self.duration
            published_revision = -1
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                if self._revision != published_revision:
                    self._publish()
                    published_revision = self._revision
                await asyncio.sleep(0.1)
        finally:
            # Release the scanner even when startup fails, times out or is cancelled.
            await asyncio.wait_for(scanner.stop(), timeout=5.0)

    def _run(self):
        try:
            asyncio.run(self._scan())
        except _ScanCancelled:
            pass
        except Exception as exc:
            self.error_message = str(exc) or type(exc).__name__
            LOGGER.exception("BLE discovery failed")
            self.message_queue.put({"type": "error", "message": self.error_message})
        finally:
            self._publish()
            self.message_queue.put({"type": "finished", "cancelled": self.stop_event.is_set(),
                                    "failed": self.error_message is not None,
                                    "device_count": len(self.devices)})


class SimulatedScanner:
    """Explicit test adapter. Production Scan Devices always uses BleakScanner."""
    DEFAULT_ADVERTISEMENTS = (
        ("AA:BB:CC:DD:EE:50", None, -75, ()),
        ("AA:BB:CC:DD:EE:50", "nRF52840_Temperature", -48, (TEMPERATURE_SERVICE,)),
        ("AA:BB:CC:DD:EE:51", "Sweat Sensor B", -58, ()),
        ("AA:BB:CC:DD:EE:52", None, -65, (TEMPERATURE_SERVICE,)),
        ("AA:BB:CC:DD:EE:53", "Other BLE device", -40, ()),
    )

    def __init__(self, detection_callback, *, advertisements=None, start_error=None,
                 start_delay=0.0, interval=0.01, stop_error=None):
        self.callback = detection_callback
        self.advertisements = self.DEFAULT_ADVERTISEMENTS if advertisements is None else advertisements
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_delay = start_delay
        self.interval = interval
        self.task = None
        self.started = threading.Event()
        self.stopped = False

    async def start(self):
        self.started.set()
        if self.start_delay:
            await asyncio.sleep(self.start_delay)
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.task = asyncio.create_task(self._emit())

    async def _emit(self):
        for address, name, rssi, services in self.advertisements:
            self.callback(SimpleNamespace(address=address, name=name),
                          SimpleNamespace(local_name=name, rssi=rssi, service_uuids=services))
            await asyncio.sleep(self.interval)

    async def stop(self):
        self.stopped = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.stop_error:
            raise RuntimeError(self.stop_error)
