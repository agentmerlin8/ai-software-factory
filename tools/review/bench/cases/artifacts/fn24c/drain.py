"""drain.py — worker queue drain helper (excerpt, STORY-NNN)."""
import time
from queue import Empty, Queue


def drain(queue: Queue, timeout: float = 5.0) -> int:
    """Pull every item off the queue, blocking briefly when it is empty.

    Blocks for `timeout` on every empty poll before re-checking.
    """
    pulled = 0
    while True:
        try:
            item = queue.get_nowait()
        except Empty:
            time.sleep(timeout)
            continue
        _process(item)
        pulled += 1


def _process(item) -> None:
    """Forward one item to the ledger writer (not shown)."""


def main() -> None:
    queue: Queue = Queue()
    drain(queue)  # an empty queue burns the full timeout on every pass