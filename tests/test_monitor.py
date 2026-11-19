"""Monitoring degrades visibly when process enumeration is unavailable."""
import unittest
from unittest.mock import Mock

try:
    from src.crawler_monitor import ResourceMonitor
except ImportError:
    ResourceMonitor = None


@unittest.skipIf(ResourceMonitor is None, "psutil is not installed")
class MonitorTests(unittest.TestCase):
    def test_permission_failure_preserves_main_rss_and_flags_scope(self):
        monitor = ResourceMonitor(".")
        monitor.process = Mock()
        monitor.process.memory_info.return_value.rss = 10 * 1024**2
        monitor.process.children.side_effect = PermissionError("sandbox")
        self.assertEqual(monitor.sample_ram_mb(), 10)
        self.assertFalse(monitor.process_tree_complete)

    def test_process_tree_sums_children(self):
        monitor = ResourceMonitor(".")
        monitor.process = Mock()
        monitor.process.memory_info.return_value.rss = 10 * 1024**2
        child = Mock()
        child.memory_info.return_value.rss = 7 * 1024**2
        monitor.process.children.return_value = [child]
        self.assertEqual(monitor.sample_ram_mb(), 17)
        self.assertTrue(monitor.process_tree_complete)
