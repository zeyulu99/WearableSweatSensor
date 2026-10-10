import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from scanning import BleScanWorker, ScanResult, SimulatedScanner, TEMPERATURE_SERVICE


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.workers = []
        self.scanners = []

    def tearDown(self):
        for worker in self.workers:
            worker.stop()
            if worker.thread.ident is not None:
                worker.thread.join(3)
                self.assertFalse(worker.thread.is_alive(), "Leaked scanner thread")

    def worker(self, duration=0.15, operation_timeout=1, **options):
        def factory(**kwargs):
            scanner = SimulatedScanner(**kwargs, **options)
            self.scanners.append(scanner)
            return scanner
        worker = BleScanWorker(scanner_factory=factory, duration=duration, operation_timeout=operation_timeout)
        self.workers.append(worker)
        return worker

    @staticmethod
    def finish(worker):
        worker.start()
        worker.thread.join(3)
        if worker.thread.is_alive():
            raise AssertionError("Scanner did not finish")
        messages = []
        while not worker.message_queue.empty():
            messages.append(worker.message_queue.get_nowait())
        return messages

    def test_discovery_deduplicates_and_updates_name_and_signal(self):
        worker = self.worker()
        messages = self.finish(worker)
        self.assertEqual(len(worker.devices), 4)
        updated = worker.devices["AA:BB:CC:DD:EE:50"]
        self.assertEqual(updated.name, "nRF52840_Temperature")
        self.assertEqual(updated.rssi, -48)
        self.assertEqual(messages[-1]["type"], "finished")
        self.assertFalse(messages[-1]["failed"])
        self.assertTrue(self.scanners[0].stopped)

    def test_unnamed_devices_remain_visible(self):
        worker = self.worker()
        self.finish(worker)
        unknown = worker.devices["AA:BB:CC:DD:EE:52"]
        self.assertEqual(unknown.name, "")
        self.assertTrue(unknown.possible_sensor)

    def test_unrelated_ble_devices_are_not_silently_dropped(self):
        worker = self.worker()
        self.finish(worker)
        self.assertIn("AA:BB:CC:DD:EE:53", worker.devices)
        self.assertFalse(worker.devices["AA:BB:CC:DD:EE:53"].possible_sensor)

    def test_empty_scan_is_success_with_zero_devices(self):
        worker = self.worker(advertisements=[])
        messages = self.finish(worker)
        self.assertEqual(worker.devices, {})
        self.assertFalse(messages[-1]["failed"])
        self.assertEqual(messages[-1]["device_count"], 0)

    def test_bluetooth_failure_is_reported_and_scanner_cleaned(self):
        worker = self.worker(start_error="Bluetooth is off")
        messages = self.finish(worker)
        self.assertEqual(worker.error_message, "Bluetooth is off")
        self.assertTrue(messages[-1]["failed"])
        self.assertTrue(self.scanners[0].stopped)

    def test_cancel_active_scan(self):
        worker = self.worker(duration=60)
        worker.start()
        deadline = time.monotonic() + 1
        while not self.scanners and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.scanners[0].started.wait(1))
        worker.stop()
        worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(worker.error_message)
        self.assertTrue(self.scanners[0].stopped)

    def test_cancel_during_slow_start(self):
        worker = self.worker(start_delay=60)
        worker.start()
        deadline = time.monotonic() + 1
        while not self.scanners and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.scanners[0].started.wait(1))
        worker.stop()
        worker.thread.join(2)
        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(worker.error_message)
        self.assertTrue(self.scanners[0].stopped)

    def test_start_timeout_releases_scanner(self):
        worker = self.worker(start_delay=60, operation_timeout=0.1)
        self.finish(worker)
        self.assertIn("in time", worker.error_message)
        self.assertTrue(self.scanners[0].stopped)

    def test_stop_before_start_never_opens_scanner(self):
        worker = self.worker()
        worker.stop()
        self.finish(worker)
        self.assertEqual(self.scanners, [])

    def test_cleanup_error_is_not_reported_as_success(self):
        worker = self.worker(stop_error="Scanner cleanup failed")
        messages = self.finish(worker)
        self.assertTrue(messages[-1]["failed"])
        self.assertIn("cleanup", worker.error_message)

    def test_invalid_address_is_ignored_and_missing_rssi_supported(self):
        worker = self.worker(advertisements=[("invalid", "Noise", -40, ()),
                                             ("aa:bb:cc:dd:ee:60", "Unnamed", None, ())])
        self.finish(worker)
        self.assertEqual(list(worker.devices), ["AA:BB:CC:DD:EE:60"])
        self.assertIsNone(worker.devices["AA:BB:CC:DD:EE:60"].rssi)

    def test_name_and_service_survive_incomplete_followup(self):
        worker = self.worker()
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:61", name=None)
        worker._detected(device, SimpleNamespace(local_name="Sweat A", rssi=-40, service_uuids=[TEMPERATURE_SERVICE]))
        worker._detected(device, SimpleNamespace(local_name=None, rssi=None, service_uuids=[]))
        result = worker.devices[device.address]
        self.assertEqual((result.name, result.rssi), ("Sweat A", -40))
        self.assertEqual(result.service_uuids, (TEMPERATURE_SERVICE,))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    unittest.main(verbosity=2)
