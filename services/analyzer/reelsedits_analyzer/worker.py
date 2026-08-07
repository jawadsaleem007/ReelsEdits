"""GPU worker loop.

Shared shape across all three GPU pools (analyzer, indexer, renderer).
Uniformity here is worth more than per-worker optimisation -- it makes the
fleet debuggable. See docs/03 section 5.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass

log = logging.getLogger("reelsedits.worker")


@dataclass(slots=True)
class JobEnvelope:
    job_id: str
    kind: str
    priority: int
    payload: dict
    estimated_vram_mb: int
    estimated_gpu_seconds: float
    trace_context: dict[str, str]


class AdmissionController:
    """Refuse work we cannot finish, rather than OOM halfway through.

    Handling OOM after the fact means a half-rendered job, a corrupted CUDA
    context, and usually a process restart. Refusing up front leaves the
    message in the stream for a worker with more headroom.
    """

    def __init__(self, total_vram_mb: int, reserve_mb: int = 2048) -> None:
        self.total = total_vram_mb
        self.reserve = reserve_mb
        self.in_flight_mb = 0

    def can_admit(self, job: JobEnvelope) -> bool:
        return self.in_flight_mb + job.estimated_vram_mb + self.reserve <= self.total

    def admit(self, job: JobEnvelope) -> None:
        self.in_flight_mb += job.estimated_vram_mb

    def release(self, job: JobEnvelope) -> None:
        self.in_flight_mb = max(0, self.in_flight_mb - job.estimated_vram_mb)


class Worker:
    """Claim from Redis Streams, process, write artefact, ack.

    Pull-based rather than push-based so the worker controls its own admission
    and batching -- a worker with 24GB VRAM decides for itself whether it can
    take another 4K job. Push cannot give it that.
    """

    def __init__(self, pool: str, streams: list[str], consumer: str) -> None:
        self.pool = pool
        self.streams = streams          # priority-ordered: p0 interactive first
        self.consumer = consumer
        self.admission = AdmissionController(
            total_vram_mb=int(os.getenv("REELSEDITS_VRAM_MB", "24576"))
        )
        self._stop = asyncio.Event()

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop.set)

        log.info("worker %s/%s started", self.pool, self.consumer)
        while not self._stop.is_set():
            job = await self._claim()
            if job is None:
                await asyncio.sleep(0.25)
                continue
            if not self.admission.can_admit(job):
                await self._release_to_stream(job)   # a bigger worker will take it
                await asyncio.sleep(0.5)
                continue

            self.admission.admit(job)
            try:
                await self._process(job)
                await self._ack(job)                 # ack ONLY after durable write
            except Exception:
                log.exception("job %s failed", job.job_id)
                await self._nack(job)
            finally:
                self.admission.release(job)

        log.info("worker %s/%s draining", self.pool, self.consumer)

    # -- transport ----------------------------------------------------------

    async def _claim(self) -> JobEnvelope | None:
        # TODO: XREADGROUP across priority streams; XAUTOCLAIM stale messages
        #       so a crashed worker's job is reclaimed rather than lost
        raise NotImplementedError

    async def _release_to_stream(self, job: JobEnvelope) -> None:
        raise NotImplementedError  # TODO

    async def _ack(self, job: JobEnvelope) -> None:
        raise NotImplementedError  # TODO

    async def _nack(self, job: JobEnvelope) -> None:
        # TODO: 3-strike rule -> quarantine to a dead-letter stream and alert.
        #       A poison-pill job that crashes workers must not take down the
        #       whole pool.
        raise NotImplementedError

    # -- work ---------------------------------------------------------------

    async def _process(self, job: JobEnvelope) -> None:
        raise NotImplementedError  # subclass implements
