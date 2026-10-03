"""Bottom bar showing the running job: stages, progress, speed and ETA."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from ps5ffpfsc.core.games import format_duration, format_size

from .compress_page import reveal
from .runner import Job, JobRunner
from .widgets import Thumbnail, button, label, restyle


class JobBar(QFrame):
    def __init__(self, runner: JobRunner) -> None:
        super().__init__()
        self.setObjectName("JobBar")
        self.runner = runner
        self.job: Job | None = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(24, 14, 24, 14)
        lay.setSpacing(16)
        self.thumb = Thumbnail(44, "gamepad")
        lay.addWidget(self.thumb)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.title = label("", "cardTitle")
        top.addWidget(self.title)
        self.steps_box = QHBoxLayout()
        self.steps_box.setSpacing(6)
        top.addLayout(self.steps_box)
        top.addStretch(1)
        self.percent = label("", "big")
        top.addWidget(self.percent)
        mid.addLayout(top)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        mid.addWidget(self.bar)

        bottom = QHBoxLayout()
        self.detail = label("", "muted")
        bottom.addWidget(self.detail, 1)
        self.stats = label("", "muted")
        bottom.addWidget(self.stats)
        mid.addLayout(bottom)
        lay.addLayout(mid, 1)

        self.open_btn = button("Show in folder", "folder-open", on_click=self._open_output)
        self.cancel_btn = button("Cancel", "stop", "danger", self._cancel)
        self.dismiss_btn = button("Dismiss", variant="ghost", on_click=self.hide)
        for b in (self.open_btn, self.cancel_btn, self.dismiss_btn):
            lay.addWidget(b)

        self._ticker = QTimer(self, interval=1000)
        self._ticker.timeout.connect(self._render)
        runner.jobStarted.connect(self._on_started)
        runner.jobChanged.connect(self._on_changed)
        runner.jobFinished.connect(self._on_finished)
        self.hide()

    def _on_started(self, job: Job) -> None:
        self.job = job
        self.thumb.set_fallback({"compress": "compress", "unpack": "unpack", "verify": "shield"}[job.kind])
        self.thumb.set_png(job.thumbnail)
        self.show()
        self._ticker.start()
        self._render()

    def _on_changed(self, job: Job) -> None:
        if job is self.job:
            self._render()

    def _on_finished(self, job: Job) -> None:
        if job is self.job:
            self._render()
            if not self.runner.busy:
                self._ticker.stop()

    def _rebuild_steps(self, job: Job) -> None:
        while self.steps_box.count():
            item = self.steps_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if len(job.stages) <= 1:
            return
        for i, st in enumerate(job.stages):
            if i:
                sep = QLabel("›")
                sep.setProperty("step", "pending")
                self.steps_box.addWidget(sep)
            lbl = QLabel(st.label)
            lbl.setProperty("stage_index", i)
            self.steps_box.addWidget(lbl)

    def _render(self) -> None:
        job = self.job
        if job is None:
            return
        if self.steps_box.property("job_id") != (job.id, len(job.stages)):
            self.steps_box.setProperty("job_id", (job.id, len(job.stages)))
            self._rebuild_steps(job)
        for i in range(self.steps_box.count()):
            w = self.steps_box.itemAt(i).widget()
            idx = w.property("stage_index") if w else None
            if idx is None:
                continue
            if job.state == "done" or idx < job.stage_index:
                state = "done"
            elif idx == job.stage_index:
                state = "failed" if job.state == "failed" else "active"
            else:
                state = "pending"
            if w.property("step") != state:
                w.setProperty("step", state)
                restyle(w)

        self.title.setText(job.title)
        self.bar.setValue(int(job.overall * 1000))
        bar_state = {"done": "done", "failed": "failed", "cancelled": "failed"}.get(job.state, "")
        if self.bar.property("state") != bar_state:
            self.bar.setProperty("state", bar_state)
            restyle(self.bar)

        queued = len(self.runner.pending())
        more = f" · {queued} more queued" if queued else ""
        if job.state == "running":
            self.percent.setText(f"{job.overall * 100:.0f}%")
            detail = job.phase or "Starting…"
            if job.stage_detail:
                detail += f" · {job.stage_detail}"
            self.detail.setText(detail + more)
            parts = []
            if job.speed:
                parts.append(f"{format_size(job.speed)}/s")
            if job.eta is not None and job.overall > 0.02:
                parts.append(f"{format_duration(job.eta)} left")
            parts.append(f"{format_duration(job.elapsed)} elapsed")
            self.stats.setText(" · ".join(parts))
        elif job.state == "done":
            self.percent.setText("Done")
            self.detail.setText(self._done_text(job) + more)
            self.stats.setText(f"took {format_duration(job.elapsed)}")
        elif job.state == "failed":
            self.percent.setText("Failed")
            self.detail.setText((job.error or "Something went wrong.") + more)
            self.stats.setText("details in Activity")
        elif job.state == "cancelled":
            self.percent.setText("Cancelled")
            self.detail.setText("Partial files were removed." + more)
            self.stats.setText("")
        self.detail.setToolTip(self.detail.text())

        active = job.state in ("running", "queued")
        self.cancel_btn.setVisible(active)
        self.dismiss_btn.setVisible(not active)
        self.open_btn.setVisible(job.state == "done" and bool(job.outputs))

    @staticmethod
    def _done_text(job: Job) -> str:
        if job.kind == "compress" and job.outputs:
            out = sum(o.get("size", 0) for o in job.outputs)
            src = sum(o.get("source_size", 0) for o in job.outputs)
            names = ", ".join(Path(o["path"]).name for o in job.outputs)
            saved = f" (−{(1 - out / src) * 100:.0f}%)" if src else ""
            return f"{names} · {format_size(src)} → {format_size(out)}{saved}"
        if job.kind == "unpack" and job.outputs:
            o = job.outputs[0]
            return f"{o.get('files', 0):,} files · {format_size(o.get('size', 0))} → {o['path']}"
        if job.kind == "verify":
            return "Structure OK — every file decoded and every compressed block passed its checksum."
        return "Finished."

    def _open_output(self) -> None:
        if self.job and self.job.outputs:
            reveal(Path(self.job.outputs[0]["path"]))

    def _cancel(self) -> None:
        if self.job and self.job.state == "running":
            self.runner.cancel(self.job)
