"""Runs jobs one at a time in the worker subprocess and tracks their progress."""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ps5ffpfsc.core import project_root
from ps5ffpfsc.core.worker import EVENT_PREFIX, PHASE_LABELS

_ids = itertools.count(1)


@dataclass
class Stage:
    id: str
    label: str
    weight: float


@dataclass
class Job:
    kind: str  # "compress" | "unpack" | "verify"
    spec: dict[str, Any]
    title: str
    subtitle: str = ""
    thumbnail: bytes | None = None
    id: int = field(default_factory=lambda: next(_ids))
    state: str = "queued"  # queued | running | done | failed | cancelled
    stages: list[Stage] = field(default_factory=list)
    stage_index: int = -1
    stage_detail: str = ""
    phase: str = ""
    stage_fraction: float = 0.0
    overall: float = 0.0
    speed: float | None = None  # bytes/s
    eta: float | None = None  # seconds
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str | None = None
    outputs: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    retries: int = 0
    oom: bool = False
    password_required: bool = False
    cancel_requested: bool = False
    _result: dict[str, Any] | None = None
    _samples: deque = field(default_factory=lambda: deque(maxlen=120))

    @property
    def stage(self) -> Stage | None:
        return self.stages[self.stage_index] if 0 <= self.stage_index < len(self.stages) else None

    @property
    def elapsed(self) -> float:
        end = self.finished_at or time.monotonic()
        return end - self.started_at if self.started_at else 0.0

    def reset_progress(self) -> None:
        self.stages, self.stage_index, self.stage_detail, self.phase = [], -1, "", ""
        self.stage_fraction = self.overall = 0.0
        self.speed = self.eta = None
        self.error, self._result, self.oom = None, None, False
        self.artifacts, self.outputs = [], []
        self._samples.clear()


_ERROR_LINE = re.compile(r"^(error\b|\[error\]|traceback\b|\w*(error|exception)\b(:|$))", re.IGNORECASE)
_WARN_LINE = re.compile(r"^(warn(ing)?\b|\[warn(ing)?\])", re.IGNORECASE)


def classify_backend_line(text: str) -> str:
    """Log level for a plain mkpfs output line (summary rows like "Errors: 0" stay plain)."""
    stripped = text.strip()
    if _ERROR_LINE.match(stripped):
        return "error"
    if _WARN_LINE.match(stripped):
        return "warn"
    return "backend"


def worker_command() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, ["--worker"]
    return sys.executable, ["-m", "ps5ffpfsc", "--worker"]


class JobRunner(QObject):
    jobChanged = Signal(object)
    jobStarted = Signal(object)
    jobFinished = Signal(object)
    queueDrained = Signal()
    log = Signal(str, str)  # level, message

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: deque[Job] = deque()
        self._current: Job | None = None
        self._proc: QProcess | None = None
        self._buffer = b""

    # ── public API ──────────────────────────────────────────────────────────

    @property
    def current(self) -> Job | None:
        return self._current

    @property
    def busy(self) -> bool:
        return self._current is not None or bool(self._queue)

    def pending(self) -> list[Job]:
        return list(self._queue)

    def submit(self, job: Job) -> Job:
        job.state = "queued"
        self._queue.append(job)
        self.jobChanged.emit(job)
        QTimer.singleShot(0, self._start_next)
        return job

    def cancel(self, job: Job) -> None:
        if job is self._current:
            self._kill_current()
        elif job in self._queue:
            self._queue.remove(job)
            job.state = "cancelled"
            self.jobFinished.emit(job)

    def cancel_all(self) -> None:
        for job in list(self._queue):
            self.cancel(job)
        if self._current:
            self._kill_current()

    # ── process management ─────────────────────────────────────────────────

    def _start_next(self) -> None:
        if self._current is not None or not self._queue:
            return
        job = self._queue.popleft()
        self._current = job
        job.reset_progress()
        job.state = "running"
        job.cancel_requested = False
        job.started_at = time.monotonic()
        job.finished_at = 0.0

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        if not getattr(sys, "frozen", False):
            root = str(project_root())
            existing = env.value("PYTHONPATH", "")
            env.insert("PYTHONPATH", root + (os.pathsep + existing if existing else ""))
        proc.setProcessEnvironment(env)
        proc.setWorkingDirectory(str(project_root()))
        if os.name == "nt":
            self._hide_console(proc)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        self._buffer = b""

        program, args = worker_command()
        self.jobStarted.emit(job)
        self.jobChanged.emit(job)
        proc.start(program, args)
        proc.write((json.dumps(job.spec) + "\n").encode())
        proc.closeWriteChannel()

    @staticmethod
    def _hide_console(proc: QProcess) -> None:
        def modifier(args: Any) -> None:
            args.flags |= 0x08000000  # CREATE_NO_WINDOW

        try:
            proc.setCreateProcessArgumentsModifier(modifier)
        except AttributeError:
            pass

    def _kill_current(self) -> None:
        job, proc = self._current, self._proc
        if job is None or proc is None:
            return
        job.cancel_requested = True
        self.log.emit("warn", f"Cancelling: {job.title}")
        pid = int(proc.processId() or 0)
        if pid <= 0:
            proc.kill()
            return
        if os.name == "nt":
            # Kill the worker and mkpfs's pool processes.
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, creationflags=0x08000000)
        else:
            try:
                # The worker calls setsid(); only signal the group once it has,
                # otherwise we would be signalling our own process group.
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGTERM)
                    QTimer.singleShot(2000, lambda: self._force_kill_group(pid))
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError):
                proc.kill()

    def _force_kill_group(self, pid: int) -> None:
        try:
            if os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    # ── output parsing ──────────────────────────────────────────────────────

    def _on_output(self) -> None:
        if self._proc is None:
            return
        self._buffer += bytes(self._proc.readAllStandardOutput().data())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            text = raw.decode("utf-8", errors="replace").rstrip("\r")
            # mkpfs redraws some lines with \r; keep only the final state.
            text = text.split("\r")[-1].rstrip()
            if text:
                self._handle_line(text)

    def _handle_line(self, text: str) -> None:
        job = self._current
        if job is None:
            return
        if not text.startswith(EVENT_PREFIX):
            if "MemoryError" in text:
                job.oom = True
            self.log.emit(classify_backend_line(text), text)
            return
        try:
            ev = json.loads(text[len(EVENT_PREFIX):])
        except ValueError:
            self.log.emit("backend", text)
            return
        kind = ev.get("t")
        if kind == "plan":
            job.stages = [Stage(s["id"], s["label"], float(s["weight"])) for s in ev["stages"]]
        elif kind == "stage":
            ids = [s.id for s in job.stages]
            if ev["id"] in ids:
                job.stage_index = ids.index(ev["id"])
            job.stage_detail = ev.get("detail") or job.stage_detail
            job.stage_fraction = 0.0
            job.phase = job.stage.label if job.stage else ""
            self._update_overall(job, None)
            self.jobChanged.emit(job)
        elif kind == "progress":
            job.phase = PHASE_LABELS.get(ev["phase"], ev["phase"].title())
            job.stage_fraction = max(0.0, min(1.0, ev["done"] / max(1, ev["total"])))
            self._update_overall(job, ev.get("bytes") or None)
            self.jobChanged.emit(job)
        elif kind == "artifact":
            job.artifacts.append(ev["path"])
        elif kind == "log":
            self.log.emit(ev.get("level", "info"), ev.get("msg", ""))
        elif kind == "result":
            job._result = ev

    def _update_overall(self, job: Job, nbytes: int | None) -> None:
        total = sum(s.weight for s in job.stages) or 1.0
        before = sum(s.weight for s in job.stages[: max(0, job.stage_index)])
        weight = job.stage.weight if job.stage else 0.0
        overall = (before + weight * job.stage_fraction) / total
        job.overall = max(job.overall, min(1.0, overall))

        now = time.monotonic()
        job._samples.append((now, job.overall, nbytes, job.stage_index))
        # Rates over the last ~10 seconds of samples.
        window = [s for s in job._samples if now - s[0] <= 10.0]
        if len(window) >= 2:
            t0, o0, _b0, _st0 = window[0]
            dt = now - t0
            if dt > 1.0 and job.overall > o0:
                job.eta = (1.0 - job.overall) * dt / (job.overall - o0)
            byte_samples = [s for s in window if s[2] is not None and s[3] == job.stage_index]
            if len(byte_samples) >= 2 and byte_samples[-1][0] - byte_samples[0][0] > 0.5:
                db = byte_samples[-1][2] - byte_samples[0][2]
                job.speed = max(0.0, db / (byte_samples[-1][0] - byte_samples[0][0]))
            elif nbytes is None:
                job.speed = None

    # ── completion ──────────────────────────────────────────────────────────

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and self._current is not None:
            self._current._result = {"ok": False, "error": "Could not start the worker process."}
            self._on_finished(-1, QProcess.ExitStatus.CrashExit)

    def _on_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        job, proc = self._current, self._proc
        if job is None:
            return
        self._on_output()
        if self._buffer.strip():
            self._handle_line(self._buffer.decode("utf-8", errors="replace").strip())
            self._buffer = b""
        self._current, self._proc = None, None
        if proc is not None:
            proc.deleteLater()

        job.finished_at = time.monotonic()
        result = job._result or {}
        if job.cancel_requested:
            job.state = "cancelled"
            self._cleanup(job)
            self.log.emit("warn", f"Cancelled: {job.title}")
        elif result.get("ok"):
            job.state = "done"
            job.overall = 1.0
            job.outputs = result.get("outputs", [])
        else:
            crashed = status == QProcess.ExitStatus.CrashExit or not result
            job.oom = job.oom or bool(result.get("oom")) or (crashed and job.kind == "compress")
            job.password_required = bool(result.get("password"))
            job.error = result.get("error") or (
                "The worker stopped unexpectedly — most likely it ran out of memory."
                if crashed else f"The worker exited with code {code}."
            )
            self._cleanup(job)
            if self._retry_with_fewer_cores(job):
                QTimer.singleShot(0, self._start_next)
                return
            job.state = "failed"
            self.log.emit("error", f"Failed: {job.title} — {job.error}")

        self.jobChanged.emit(job)
        self.jobFinished.emit(job)
        if not self._queue:
            self.queueDrained.emit()
        QTimer.singleShot(0, self._start_next)

    def _retry_with_fewer_cores(self, job: Job) -> bool:
        if job.kind != "compress" or not job.oom or job.retries >= 2:
            return False
        cpu = int(job.spec.get("cpu", 0)) or min(4, os.cpu_count() or 4)
        if cpu <= 1:
            return False
        job.spec = {**job.spec, "cpu": max(1, cpu // 2)}
        job.retries += 1
        self.log.emit("warn", f"Out of memory — retrying {job.title} with {job.spec['cpu']} "
                              f"CPU core(s) (attempt {job.retries + 1}).")
        job.state = "queued"
        job.reset_progress()
        self._queue.appendleft(job)
        self.jobChanged.emit(job)
        return True

    def _cleanup(self, job: Job) -> None:
        """Delete partial outputs and temp folders the failed job created."""
        for path_str in reversed(job.artifacts):
            path = Path(path_str)
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                elif path.exists():
                    path.unlink()
            except OSError as exc:
                self.log.emit("warn", f"Could not remove {path}: {exc}")
