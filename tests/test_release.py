import asyncio
import csv
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core import BleStreamWorker, DeviceEntry, DeviceStore, FakeBleStreamWorker


class MockBleClient:
    instances = []
    fail_connect = False
    slow_connect = False
    fail_start = False
    fail_stop = False
    disconnect_after = None

    def __init__(self, address, disconnected_callback=None, timeout=None):
        self.address = address
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self.events = []
        self.emitter = None
        self.callback = None
        self.connect_started = threading.Event()
        self.instances.append(self)

    async def connect(self):
        self.connect_started.set()
        if self.slow_connect:
            await asyncio.sleep(60)
        if self.fail_connect:
            raise ConnectionError("Simulated unavailable sensor")
        self.is_connected = True
        self.events.append("connect")

    async def start_notify(self, uuid, callback):
        self.events.append("subscribe")
        self.callback = callback

    async def write_gatt_char(self, uuid, data, response=True):
        self.events.append("start" if data[0] else "stop")
        if data[0]:
            if self.fail_start:
                raise RuntimeError("Simulated start failure")
            self.emitter = asyncio.create_task(self.emit())
        elif self.fail_stop:
            raise RuntimeError("Simulated stop failure")

    async def emit(self):
        count = 0
        while self.is_connected:
            self.callback(None, bytearray((-1234-count).to_bytes(2, "little", signed=True)))
            count += 1
            if self.disconnect_after is not None and count >= self.disconnect_after:
                self.is_connected = False
                self.disconnected_callback(self)
                return
            await asyncio.sleep(0.02)

    async def stop_notify(self, uuid):
        self.events.append("unsubscribe")
        if self.emitter:
            self.emitter.cancel()
            await asyncio.gather(self.emitter, return_exceptions=True)

    async def disconnect(self):
        self.events.append("disconnect")
        self.is_connected = False
        if self.emitter:
            self.emitter.cancel()
            await asyncio.gather(self.emitter, return_exceptions=True)


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for test condition")


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sweat_release_test_")
        self.root = Path(self.temp.name)
        self.workers = []
        MockBleClient.instances = []
        for attr in ("fail_connect", "slow_connect", "fail_start", "fail_stop"):
            setattr(MockBleClient, attr, False)
        MockBleClient.disconnect_after = None

    def tearDown(self):
        for worker in self.workers:
            worker.stop()
        for worker in self.workers:
            if worker.thread.ident is not None:
                worker.thread.join(4)
                self.assertFalse(worker.thread.is_alive(), "Leaked acquisition thread")
        self.temp.cleanup()

    def worker(self, name="test", mac="AA:BB:CC:DD:EE:01", fake=False, **kwargs):
        klass = FakeBleStreamWorker if fake else BleStreamWorker
        if not fake:
            kwargs.setdefault("client_factory", MockBleClient)
        worker = klass(DeviceEntry(name, mac), self.root / "实验 数据", **kwargs)
        self.workers.append(worker)
        return worker

    @staticmethod
    def rows(worker):
        with worker.csv_path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_device_add_update_and_restart(self):
        path = self.root / "配置" / "devices.csv"
        store = DeviceStore(path)
        store.upsert(DeviceEntry("传感器 一", "aa:bb:cc:dd:ee:01"))
        store.upsert(DeviceEntry("Updated", "AA:BB:CC:DD:EE:01"))
        store.upsert(DeviceEntry("Second", "AA:BB:CC:DD:EE:02"))
        entries = DeviceStore(path).load()
        self.assertEqual(len(entries), 2)
        self.assertEqual({e.name for e in entries}, {"Updated", "Second"})

    def test_first_run_defaults_and_no_overwrite_on_restart(self):
        seed = self.root / "seed.csv"
        DeviceStore(seed).save([DeviceEntry("Default", "AA:BB:CC:DD:EE:01")])
        store = DeviceStore(self.root / "config.csv", default_path=seed)
        self.assertEqual(store.load()[0].name, "Default")
        store.upsert(DeviceEntry("Renamed", "AA:BB:CC:DD:EE:01"))
        self.assertEqual(store.load()[0].name, "Renamed")

    def test_public_defaults_are_empty_and_new_device_survives_restart(self):
        seed = Path(__file__).resolve().parents[1] / "resources" / "default_devices.csv"
        store = DeviceStore(self.root / "public-config.csv", default_path=seed)
        self.assertEqual(store.load(), [])
        entry = DeviceEntry("New sensor", "AA:BB:CC:DD:EE:01")
        store.upsert(entry)
        self.assertEqual(DeviceStore(store.csv_path, default_path=seed).load(), [entry])

    def test_bad_config_is_not_replaced(self):
        path = self.root / "bad.csv"
        path.write_text("broken\ncontents", encoding="utf-8")
        with self.assertRaises(ValueError):
            DeviceStore(path).load()
        self.assertEqual(path.read_text(), "broken\ncontents")

    def test_atomic_config_preserves_previous_on_failure(self):
        store = DeviceStore(self.root / "devices.csv")
        store.save([DeviceEntry("Original", "AA:BB:CC:DD:EE:01")])
        before = store.csv_path.read_bytes()
        with patch("core.os.replace", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                store.save([DeviceEntry("New", "AA:BB:CC:DD:EE:02")])
        self.assertEqual(store.csv_path.read_bytes(), before)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_invalid_device_rejected(self):
        with self.assertRaises(ValueError):
            DeviceStore(self.root / "devices.csv").upsert(DeviceEntry("Name", "not-a-mac"))

    def test_signed_two_and_four_byte_payloads(self):
        worker = self.worker()
        worker._write_headers()
        for size in (2, 4):
            worker._notification_handler(None, bytearray((-1234).to_bytes(size, "little", signed=True)))
        self.assertEqual([r[1] for r in worker.records], [-123.4, -123.4])

    def test_malformed_payloads_do_not_become_samples(self):
        worker = self.worker()
        worker._write_headers()
        for value in (b"", b"\x01", b"\x01\x02\x03", b"\x00"*8):
            worker._notification_handler(None, bytearray(value))
        self.assertEqual(worker.sample_count, 0)
        self.assertEqual(worker.discarded_samples, 4)
        self.assertEqual(self.rows(worker), [])

    def test_unique_files_for_same_name_and_concurrent_sessions(self):
        workers = [self.worker("同名设备", f"AA:BB:CC:DD:EE:{i:02X}", fake=True, sample_interval=0.02)
                   for i in range(3)]
        for worker in workers:
            worker.start()
        wait_for(lambda: all(w.sample_count >= 3 for w in workers))
        self.assertEqual(len({w.csv_path for w in workers}), 3)
        for worker in workers:
            worker.stop()
            worker.thread.join(2)
            self.assertEqual(len(self.rows(worker)), worker.sample_count)

    def test_exclusive_creation_does_not_overwrite(self):
        first = self.worker()
        first._write_headers()
        first._record_sample(0, 12.3)
        second = self.worker()
        second.csv_path = first.csv_path
        second.txt_path = first.txt_path
        before = first.csv_path.read_bytes()
        with self.assertRaises(FileExistsError):
            second._write_headers()
        self.assertEqual(first.csv_path.read_bytes(), before)

    def test_stop_one_sensor_does_not_stop_others(self):
        workers = [self.worker(str(i), f"AA:BB:CC:DD:EE:{i:02X}", fake=True, sample_interval=0.02)
                   for i in range(3)]
        for worker in workers:
            worker.start()
        wait_for(lambda: all(w.sample_count >= 3 for w in workers))
        workers[0].stop()
        workers[0].thread.join(2)
        stopped_count = workers[0].sample_count
        before = [w.sample_count for w in workers[1:]]
        wait_for(lambda: all(w.sample_count >= n+3 for w,n in zip(workers[1:],before)))
        self.assertEqual(workers[0].sample_count, stopped_count)

    def test_event_flags_and_matching_txt_csv(self):
        worker = self.worker(fake=True, sample_interval=0.03)
        worker.start()
        wait_for(lambda: worker.sample_count >= 2)
        for color in ("blue", "yellow", "red", "green"):
            worker.add_flag(color, *worker.records[-1])
        worker.stop()
        worker.thread.join(2)
        flags = [r["event_flag"] for r in self.rows(worker) if r["event_flag"]]
        self.assertEqual(flags, ["blue", "yellow", "red", "green"])
        self.assertEqual(worker.txt_path.read_bytes(), worker.csv_path.read_bytes())

    def test_notification_subscription_precedes_start_and_cleanup(self):
        worker = self.worker()
        worker.start()
        wait_for(lambda: worker.sample_count >= 2)
        worker.stop()
        worker.thread.join(2)
        self.assertIsNone(worker.error_message)
        self.assertEqual(MockBleClient.instances[0].events,
                         ["connect", "subscribe", "start", "stop", "unsubscribe", "disconnect"])

    def test_unexpected_disconnect_finishes_and_preserves_data(self):
        MockBleClient.disconnect_after = 3
        worker = self.worker()
        worker.start()
        worker.thread.join(3)
        self.assertFalse(worker.thread.is_alive())
        self.assertIn("disconnected", worker.error_message.lower())
        self.assertEqual(len(self.rows(worker)), 3)

    def test_connect_failure_finishes_without_samples(self):
        MockBleClient.fail_connect = True
        worker = self.worker()
        worker.start()
        worker.thread.join(3)
        self.assertFalse(worker.thread.is_alive())
        self.assertIn("unavailable", worker.error_message)
        self.assertEqual(self.rows(worker), [])

    def test_stop_during_pending_connect(self):
        MockBleClient.slow_connect = True
        worker = self.worker()
        worker.start()
        wait_for(lambda: MockBleClient.instances and MockBleClient.instances[0].connect_started.is_set())
        worker.stop()
        worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(worker.error_message)
        self.assertIn("disconnect", MockBleClient.instances[0].events)

    def test_connection_operation_timeout(self):
        MockBleClient.slow_connect = True
        worker = self.worker(operation_timeout=0.1)
        worker.start()
        worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())
        self.assertIn("timed out", worker.error_message)

    def test_failed_start_still_cleans_up(self):
        MockBleClient.fail_start = True
        worker = self.worker()
        worker.start()
        worker.thread.join(2)
        self.assertEqual(MockBleClient.instances[0].events[-3:], ["stop", "unsubscribe", "disconnect"])
        self.assertIn("start failure", worker.error_message)

    def test_cleanup_continues_if_stop_write_fails(self):
        MockBleClient.fail_stop = True
        worker = self.worker()
        worker.start()
        wait_for(lambda: worker.sample_count >= 2)
        worker.stop()
        worker.thread.join(2)
        self.assertEqual(MockBleClient.instances[0].events[-2:], ["unsubscribe", "disconnect"])
        self.assertFalse(worker.thread.is_alive())

    def test_disk_error_in_notification_stops_acquisition(self):
        worker = self.worker()
        with patch.object(worker, "_append_record", side_effect=OSError("simulated disk full")):
            worker.start()
            worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())
        self.assertIn("disk full", worker.error_message)

    def test_simulated_failure_is_isolated(self):
        failed = self.worker("fail", fake=True, sample_interval=0.02, fail_after=3)
        healthy = self.worker("healthy", "AA:BB:CC:DD:EE:02", fake=True, sample_interval=0.02)
        failed.start()
        healthy.start()
        failed.thread.join(2)
        wait_for(lambda: healthy.sample_count >= 6)
        self.assertFalse(failed.thread.is_alive())
        self.assertTrue(healthy.thread.is_alive())
        self.assertEqual(len(self.rows(failed)), 3)


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
