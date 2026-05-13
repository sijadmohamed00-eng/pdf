#!/usr/bin/env python3
"""
memory_manager.py
═════════════════
Smart Memory & Batch Processing Manager for Enterprise OCR System
- Dynamic batch sizing based on available RAM
- Per-page memory budget enforcement
- Disk-spill for intermediate results
- Progress callback support
"""

import gc
import os
import json
import time
import psutil
import logging
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger("memory_manager")

# ─────────────────────────────────────────────────────────────────────────────
# Memory thresholds
# ─────────────────────────────────────────────────────────────────────────────

RAM_SAFETY_MARGIN   = 0.25    # keep 25% RAM free at all times
RAM_CRITICAL        = 0.85    # if used% > this → force GC + reduce workers
RAM_EMERGENCY       = 0.92    # if used% > this → pause processing 2 s
MIN_WORKERS         = 1
MAX_WORKERS_HARD    = 8
SPILL_THRESHOLD_MB  = 50      # spill results to disk if accumulated > 50 MB


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def ram_used_percent() -> float:
    return psutil.virtual_memory().percent / 100.0


def available_ram_mb() -> float:
    return psutil.virtual_memory().available / (1024 * 1024)


def process_ram_mb() -> float:
    """RAM used by this Python process (RSS)."""
    return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)


def suggest_workers(requested: int) -> int:
    """Reduce worker count if RAM is tight."""
    used = ram_used_percent()
    if used > RAM_CRITICAL:
        w = max(MIN_WORKERS, requested // 2)
        log.warning("RAM %.0f%% used → reducing workers to %d", used * 100, w)
        return w
    return min(requested, MAX_WORKERS_HARD)


def suggest_batch_size(requested: int, dpi: int) -> int:
    """
    Estimate a safe batch size.
    A 200-DPI A4 page ≈ 4 MB in RAM as PIL RGB.
    """
    avail = available_ram_mb()
    # Rough per-page estimate: (A4 pixels at DPI) * 3 bytes RGB / 1024^2
    w_px = int(8.27  * dpi)
    h_px = int(11.69 * dpi)
    per_page_mb = (w_px * h_px * 3) / (1024 * 1024)
    # Use at most 40% of available RAM for page images
    safe_pages = int((avail * 0.40) / per_page_mb)
    safe_pages = max(1, min(safe_pages, requested))
    if safe_pages < requested:
        log.info("RAM-safe batch size: %d (requested %d)", safe_pages, requested)
    return safe_pages


# ─────────────────────────────────────────────────────────────────────────────
# Result spill manager
# ─────────────────────────────────────────────────────────────────────────────

class SpillManager:
    """
    Accumulates PageResult objects in memory; spills to NDJSON on disk
    when the in-memory buffer exceeds SPILL_THRESHOLD_MB.
    Results are always returned in page order.
    """

    def __init__(self, spill_dir: Optional[Path] = None):
        self._spill_dir  = spill_dir or Path(tempfile.mkdtemp(prefix="spill_"))
        self._spill_dir.mkdir(parents=True, exist_ok=True)
        self._buffer:    list[dict] = []
        self._spill_idx: int        = 0
        self._lock = threading.Lock()
        self._spill_files: list[Path] = []

    def add(self, result_dict: dict):
        with self._lock:
            self._buffer.append(result_dict)
            self._maybe_spill()

    def _maybe_spill(self):
        import sys
        buf_size = sys.getsizeof(json.dumps(self._buffer)) / (1024 * 1024)
        if buf_size >= SPILL_THRESHOLD_MB:
            self._flush()

    def _flush(self):
        if not self._buffer:
            return
        path = self._spill_dir / f"spill_{self._spill_idx:04d}.ndjson"
        with open(path, "w", encoding="utf-8") as f:
            for item in self._buffer:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        self._spill_files.append(path)
        self._spill_idx += 1
        self._buffer.clear()
        gc.collect()
        log.debug("Spilled %d results → %s", len(self._buffer), path)

    def collect_all(self) -> list[dict]:
        """Merge spill files + in-memory buffer; return sorted by page_num."""
        with self._lock:
            self._flush()

        results = []
        for path in self._spill_files:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        results.append(json.loads(line))

        results.sort(key=lambda r: r.get("page_num", 0))
        return results

    def cleanup(self):
        import shutil
        shutil.rmtree(self._spill_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Adaptive batch executor
# ─────────────────────────────────────────────────────────────────────────────

class AdaptiveBatchExecutor:
    """
    Runs OCR page tasks in adaptive batches with:
    - Dynamic worker scaling based on RAM
    - Emergency pause when RAM is critical
    - Spill-to-disk for large jobs
    - Progress callback after each page
    """

    def __init__(
        self,
        process_fn:       Callable,    # fn(page_num) → dict
        total_pages:      int,
        initial_batch:    int  = 10,
        initial_workers:  int  = 4,
        dpi:              int  = 200,
        progress_cb:      Optional[Callable[[int, int], None]] = None,
        spill_dir:        Optional[Path] = None,
    ):
        self.process_fn      = process_fn
        self.total_pages     = total_pages
        self.initial_batch   = initial_batch
        self.initial_workers = initial_workers
        self.dpi             = dpi
        self.progress_cb     = progress_cb
        self.spill           = SpillManager(spill_dir)
        self._processed      = 0
        self._lock           = threading.Lock()

    def _notify(self):
        if self.progress_cb:
            self.progress_cb(self._processed, self.total_pages)

    def _process_page_safe(self, page_num: int) -> dict:
        """Wrapper: check RAM before running, pause if critical."""
        used = ram_used_percent()
        if used > RAM_EMERGENCY:
            log.warning("RAM %.0f%% — emergency pause 2s", used * 100)
            time.sleep(2)
            gc.collect()

        result = self.process_fn(page_num)

        with self._lock:
            self._processed += 1
            self.spill.add(result if isinstance(result, dict) else vars(result))
            self._notify()

        return result

    def run(self) -> list[dict]:
        """Execute all pages and return sorted results."""
        page_nums  = list(range(self.total_pages))
        batch_size = suggest_batch_size(self.initial_batch, self.dpi)
        workers    = suggest_workers(self.initial_workers)

        log.info(
            "AdaptiveBatchExecutor: %d pages | batch=%d | workers=%d | RAM=%.0f%%",
            self.total_pages, batch_size, workers, ram_used_percent() * 100,
        )

        batches = [
            page_nums[i: i + batch_size]
            for i in range(0, self.total_pages, batch_size)
        ]

        for b_idx, batch in enumerate(batches):
            log.info(
                "Batch %d/%d | pages %d–%d | RAM=%.0f%% | proc_RAM=%.0f MB",
                b_idx + 1, len(batches),
                batch[0], batch[-1],
                ram_used_percent() * 100,
                process_ram_mb(),
            )

            # Dynamically re-check workers each batch
            w = suggest_workers(workers)

            with ThreadPoolExecutor(max_workers=w) as pool:
                futures = {pool.submit(self._process_page_safe, pg): pg for pg in batch}
                for fut in as_completed(futures):
                    try:
                        fut.result()
                    except Exception as exc:
                        pg = futures[fut]
                        log.error("Page %d executor error: %s", pg, exc)
                        self.spill.add({
                            "page_num": pg,
                            "text": "",
                            "is_rtl": False,
                            "has_image": False,
                            "error": str(exc),
                        })
                        with self._lock:
                            self._processed += 1
                            self._notify()

            # Post-batch memory housekeeping
            gc.collect()

            # Check if we should shrink batch for next round
            new_batch = suggest_batch_size(batch_size, self.dpi)
            if new_batch < batch_size:
                log.info("Shrinking batch size %d → %d due to RAM pressure", batch_size, new_batch)
                batch_size = new_batch

        results = self.spill.collect_all()
        self.spill.cleanup()
        return results


# ─────────────────────────────────────────────────────────────────────────────
# RAM monitor (background thread)
# ─────────────────────────────────────────────────────────────────────────────

class RAMMonitor(threading.Thread):
    """
    Lightweight background thread that logs RAM stats every N seconds.
    Can be attached to a job for telemetry.
    """

    def __init__(self, interval: float = 5.0, job_id: str = ""):
        super().__init__(daemon=True)
        self.interval = interval
        self.job_id   = job_id
        self._stop    = threading.Event()
        self.peak_mb  = 0.0

    def run(self):
        while not self._stop.wait(self.interval):
            mb   = process_ram_mb()
            used = ram_used_percent()
            self.peak_mb = max(self.peak_mb, mb)
            log.debug("[%s] RAM monitor: proc=%.0f MB | sys=%.0f%%", self.job_id, mb, used * 100)

            if used > RAM_CRITICAL:
                log.warning("[%s] RAM CRITICAL %.0f%% — forcing GC", self.job_id, used * 100)
                gc.collect()

    def stop(self):
        self._stop.set()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: get system info dict
# ─────────────────────────────────────────────────────────────────────────────

def system_info() -> dict:
    vm  = psutil.virtual_memory()
    cpu = psutil.cpu_count(logical=True)
    return {
        "cpu_cores":      cpu,
        "ram_total_mb":   vm.total   // (1024 * 1024),
        "ram_avail_mb":   vm.available // (1024 * 1024),
        "ram_used_pct":   round(vm.percent, 1),
        "proc_ram_mb":    round(process_ram_mb(), 1),
        "safe_workers":   suggest_workers(MAX_WORKERS_HARD),
        "safe_batch_200": suggest_batch_size(10, 200),
    }
