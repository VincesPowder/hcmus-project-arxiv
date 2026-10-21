"""Sample RAM and disk usage while a stage runs (for the performance section of the report)."""
import csv
import json
import os
import threading
import time
from pathlib import Path

import psutil


def directory_size_bytes(directory) -> int:
    total = 0
    for root, _, file_names in os.walk(directory):
        for file_name in file_names:
            try:
                total += os.path.getsize(os.path.join(root, file_name))
            except OSError:
                pass
    return total


class ResourceMonitor:
    """Context manager writing metrics/<run_name>_resources.csv and a summary json."""

    def __init__(self, run_name, data_dir, metrics_dir="metrics", interval_seconds=5, disk_interval_seconds=60):
        self.run_name = run_name
        self.data_dir = Path(data_dir)
        self.metrics_dir = Path(metrics_dir)
        self.interval_seconds = interval_seconds
        self.disk_interval_seconds = disk_interval_seconds
        self._stop_event = threading.Event()
        self._rss_samples_mb = []
        self._disk_samples_bytes = []

    def _total_rss_mb(self, process) -> float:
        total = process.memory_info().rss
        for child in process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass
        return total / 1024 / 1024

    def _run(self):
        process = psutil.Process()
        last_disk_time, disk_bytes = 0.0, 0
        with open(self.metrics_dir / f"{self.run_name}_resources.csv", "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["unix_time", "rss_mb", "disk_bytes"])
            while not self._stop_event.is_set():
                now = time.time()
                if now - last_disk_time >= self.disk_interval_seconds:
                    disk_bytes = directory_size_bytes(self.data_dir) if self.data_dir.exists() else 0
                    self._disk_samples_bytes.append(disk_bytes)
                    last_disk_time = now
                rss_mb = self._total_rss_mb(process)
                self._rss_samples_mb.append(rss_mb)
                writer.writerow([round(now, 1), round(rss_mb, 1), disk_bytes])
                handle.flush()
                self._stop_event.wait(self.interval_seconds)

    def __enter__(self):
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._stop_event.set()
        self._thread.join()
        final_disk = directory_size_bytes(self.data_dir) if self.data_dir.exists() else 0
        self._disk_samples_bytes.append(final_disk)
        summary = {
            "run_name": self.run_name,
            "peak_rss_mb": round(max(self._rss_samples_mb, default=0), 1),
            "mean_rss_mb": round(sum(self._rss_samples_mb) / max(len(self._rss_samples_mb), 1), 1),
            "peak_disk_bytes": max(self._disk_samples_bytes, default=0),
            "final_disk_bytes": final_disk,
        }
        (self.metrics_dir / f"{self.run_name}_resource_summary.json").write_text(json.dumps(summary, indent=2))
