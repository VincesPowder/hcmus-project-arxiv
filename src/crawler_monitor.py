"""Resource monitor: peak/average RAM (psutil sampling) and disk growth."""
import shutil
import threading
import time

import psutil


class ResourceMonitor:
    """Sample process-tree RSS and disk usage every `interval_seconds` in a thread."""

    def __init__(self, disk_path, interval_seconds=1.0):
        self.disk_path = str(disk_path)
        self.interval_seconds = interval_seconds
        self.ram_samples_mb = []
        self.disk_samples_mb = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.process = psutil.Process()
        self.baseline_disk_mb = None

    def sample_ram_mb(self):
        """RSS of this process plus its children, in MB."""
        total = self.process.memory_info().rss
        for child in self.process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass
        return total / 1024 ** 2

    def sample_disk_mb(self):
        """Used space of the filesystem holding the corpus, in MB."""
        return shutil.disk_usage(self.disk_path).used / 1024 ** 2

    def run(self):
        while not self.stop_event.is_set():
            self.ram_samples_mb.append(self.sample_ram_mb())
            self.disk_samples_mb.append(self.sample_disk_mb())
            time.sleep(self.interval_seconds)

    def start(self):
        self.baseline_disk_mb = self.sample_disk_mb()
        self.thread.start()

    def stop(self):
        """Stop sampling and return the summary dict."""
        self.stop_event.set()
        self.thread.join()
        ram, disk = self.ram_samples_mb or [0.0], self.disk_samples_mb or [self.baseline_disk_mb]
        return {"max_ram_mb": max(ram), "avg_ram_mb": sum(ram) / len(ram),
                "n_ram_samples": len(ram),
                "max_disk_growth_mb": max(disk) - self.baseline_disk_mb}
