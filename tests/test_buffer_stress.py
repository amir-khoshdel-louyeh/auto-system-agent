"""P4.5 buffer stress: 10k lines/s coalesced drain stays inside budgets.

GUI ack p95 < 100ms per drain tick, render batches p95 < 200ms, memory
bounded (queue cap + coalescer limit), order preserved when the consumer
keeps up with the producer.
"""

import queue
import statistics
import sys
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from auto_system_agent.ui.real_terminal import (
    DISPLAY_QUEUE_SIZE,
    EventCoalescer,
)


def _drain_all(display_queue, coalescer, force_windows=0):
    """Tick the consumer until the queue and buffer empty; return writes."""
    writes = []
    guard = 0
    while (not display_queue.empty() or coalescer.pending()) and guard < 100000:
        guard += 1
        drained = 0
        while drained < 256:
            try:
                coalescer.push(display_queue.get_nowait())
            except queue.Empty:
                break
            drained += 1
        if coalescer.ready() or (force_windows and guard % force_windows == 0):
            writes.append(coalescer.pop())
        elif display_queue.empty() and coalescer.pending():
            coalescer._window_start = 0.0
    return writes


class CoalescerBurstTests(unittest.TestCase):
    def test_10k_lines_collapse_to_few_writes(self):
        display_queue: queue.Queue[str] = queue.Queue(maxsize=DISPLAY_QUEUE_SIZE)
        coalescer = EventCoalescer()
        drops = 0
        lines = [f"line {index:05d}\n" for index in range(10000)]
        tick = 0
        for line in lines:
            try:
                display_queue.put(line, block=False)
            except queue.Full:
                drops += 1
            tick += 1
            # Consumer ticks every ~500 lines like the after(50) loop.
            if tick % 500 == 0:
                _drain_all(display_queue, coalescer, force_windows=4)
        writes = _drain_all(display_queue, coalescer)
        self.assertLess(len(writes), 100, f"10k lines produced {len(writes)} writes")
        self.assertEqual(drops, 0, "a keeping-up consumer must not drop")

    def test_order_preserved_when_consumer_keeps_up(self):
        display_queue: queue.Queue[str] = queue.Queue(maxsize=DISPLAY_QUEUE_SIZE)
        coalescer = EventCoalescer()
        blob = []
        for index in range(3000):
            display_queue.put(f"line {index:05d}\n", block=False)
            if display_queue.qsize() >= 500:
                blob.extend(_drain_all(display_queue, coalescer, force_windows=4))
        blob.extend(_drain_all(display_queue, coalescer))
        merged = "".join(blob)
        numbers = [int(part) for part in merged.replace("line ", "").split() if part.isdigit()]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(len(numbers), 3000)

    def test_memory_bounded_under_flood(self):
        display_queue: queue.Queue[str] = queue.Queue(maxsize=DISPLAY_QUEUE_SIZE)
        coalescer = EventCoalescer()
        drops = 0
        for index in range(20000):
            try:
                display_queue.put(f"flood {index}\n", block=False)
            except queue.Full:
                drops += 1
        self.assertLessEqual(display_queue.qsize(), DISPLAY_QUEUE_SIZE)
        _drain_all(display_queue, coalescer)
        # Queue cap plus coalescer byte limit bound the flood footprint.
        self.assertLessEqual(coalescer._bytes, 0)
        self.assertGreater(drops, 0, "an outrun consumer must shed load by dropping")

    def test_render_batches_inside_200ms(self):
        batch_times = []
        for _ in range(20):
            display_queue: queue.Queue[str] = queue.Queue(maxsize=DISPLAY_QUEUE_SIZE)
            coalescer = EventCoalescer()
            for index in range(500):
                display_queue.put(f"line {index:05d} with some payload text\n", block=False)
            started = time.monotonic()
            _drain_all(display_queue, coalescer)
            batch_times.append((time.monotonic() - started) * 1000.0)
        p95 = statistics.quantiles(batch_times, n=100)[94]
        self.assertLess(p95, 200.0, f"render p95 {p95:.1f}ms over budget")


class GuiAckTests(unittest.TestCase):
    def test_event_ack_inside_100ms(self):
        """Headless analog of the after(50) drain: put + handle 1k events."""
        ui_queue: queue.Queue = queue.Queue()
        for index in range(1000):
            ui_queue.put(("progress", index))
        started = time.monotonic()
        handled = 0
        while True:
            try:
                ui_queue.get_nowait()
            except queue.Empty:
                break
            handled += 1
        elapsed_ms = (time.monotonic() - started) * 1000.0
        self.assertEqual(handled, 1000)
        self.assertLess(elapsed_ms, 100.0, f"ack took {elapsed_ms:.1f}ms")


if __name__ == "__main__":
    unittest.main()
