"""Job worker subprocess.

The GUI starts ``<app> --worker`` and writes one JSON job spec to its stdin.
The worker runs the job and reports back on stdout: protocol events are lines
starting with :data:`EVENT_PREFIX` followed by JSON, and any other line
(mkpfs's own output) is plain log text.

Events (``t`` field):
    plan      {"stages": [{"id", "label", "weight"}]}
    stage     {"id", "detail"}
    progress  {"phase", "done", "total", "bytes"}
    artifact  {"path"}      partial output to delete if the job dies
    log       {"level", "msg"}
    result    {"ok", "error", "oom", "outputs": [...]}
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from . import archives
from .games import (
    AMPR_INDEX_NAME,
    AMPR_SPRX_FILES,
    IMAGE_SUFFIXES,
    SourceKind,
    find_sources,
    folder_stats,
    is_apr_game,
    read_folder_game_info,
    safe_filename,
    title_id_from_name,
    unique_path,
)

EVENT_PREFIX = "@@ffpfsc "

#: Phases mkpfs reports through its Progress helper, as shown to the user.
PHASE_LABELS = {
    "scan": "Scanning files",
    "read": "Reading files",
    "exfat": "Building exFAT",
    "compress": "Compressing",
    "write": "Writing image",
    "verify": "Verifying",
    "compare": "Comparing with source",
    "extract": "Extracting",
    "archive": "Extracting archive",
}

GB = 1024**3


class JobError(Exception):
    pass


class Emitter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_progress: dict[str, float] = {}
        self.stage_ids: list[str] = []
        self.current_stage: str | None = None

    def send(self, **event: Any) -> None:
        line = EVENT_PREFIX + json.dumps(event, separators=(",", ":")) + "\n"
        with self._lock:
            sys.stdout.write(line)
            sys.stdout.flush()

    def log(self, level: str, msg: str) -> None:
        self.send(t="log", level=level, msg=msg)

    def plan(self, stages: list[tuple[str, str, float]]) -> None:
        self.stage_ids = [s[0] for s in stages]
        self.send(t="plan", stages=[{"id": i, "label": label, "weight": w} for i, label, w in stages])

    def stage(self, stage_id: str, detail: str = "") -> None:
        self.current_stage = stage_id
        self.send(t="stage", id=stage_id, detail=detail)

    def progress(self, phase: str, done: int, total: int, nbytes: int = 0) -> None:
        # mkpfs reports very frequently; ~8 updates/s is plenty for the UI.
        now = time.monotonic()
        if done < total and now - self._last_progress.get(phase, 0.0) < 0.12:
            return
        self._last_progress[phase] = now
        self.send(t="progress", phase=phase, done=int(done), total=int(max(total, 1)), bytes=int(nbytes))


def _install_mkpfs_hooks(em: Emitter) -> None:
    """Route mkpfs's terminal progress bars into protocol events."""
    from mkpfs import pbar

    def step(self: Any, phase: str, done: int, total: int, bytes_processed: int = 0) -> None:
        em.progress(phase, done, total, bytes_processed)

    def status(self: Any, message: str) -> None:
        text = message.strip()
        if text:
            print(text, flush=True)

    pbar.Progress.step = step  # type: ignore[method-assign]
    pbar.Progress.status = status  # type: ignore[method-assign]


def _run_mkpfs(argv: list[str]) -> int:
    from mkpfs.cli import cli_mkpfs_main

    print("mkpfs " + " ".join(argv), flush=True)
    try:
        return int(cli_mkpfs_main(argv) or 0)
    except SystemExit as exc:  # argparse errors
        return int(exc.code or 0) if isinstance(exc.code, int) else 1


# ── Compress ────────────────────────────────────────────────────────────────


def _auto_cpu_cap(source_bytes: int, requested: int, em: Emitter) -> int:
    """Limit parallel workers for huge games when the user left cores on Auto.

    Every mkpfs worker buffers compressed blocks in RAM; on typical PCs one
    worker per core runs out of memory on 30+ GB games.
    """
    if requested != 0 or source_bytes <= 10 * GB:
        return requested
    cores = os.cpu_count() or 4
    cap = min(2 if source_bytes > 30 * GB else 4, cores)
    em.log("info", f"Source is {source_bytes / GB:.1f} GB — limiting compression to {cap} cores "
                   "to avoid running out of memory (set CPU cores manually to override).")
    return cap


def _inject_ampr(game: Path, ampr_dir: Path | None, em: Emitter) -> list[Path]:
    """Copy the AMPR emulator libs into ``game/fakelib``. Returns what was created."""
    created: list[Path] = []
    if ampr_dir is None or not ampr_dir.is_dir():
        em.log("warn", "APR title, but no AMPR emu folder is set in Settings — "
                       "compressing without fakelib. The game may not boot.")
        return created
    fakelib = game / "fakelib"
    if not fakelib.exists():
        fakelib.mkdir()
        created.append(fakelib)
    for name in AMPR_SPRX_FILES:
        src, dst = ampr_dir / name, fakelib / name
        if dst.exists():
            em.log("info", f"AMPR: {name} already present in the game.")
            continue
        if not src.is_file():
            em.log("warn", f"AMPR: {name} not found in {ampr_dir}.")
            continue
        shutil.copy2(src, dst)
        created.append(dst)
        em.log("info", f"AMPR: added fakelib/{name}")
    index = game / AMPR_INDEX_NAME
    if created and not index.exists():
        created.append(index)  # mkpfs generates it while packing
    return created


def _remove_paths(paths: list[Path]) -> None:
    for p in reversed(paths):  # files before the folder that holds them
        try:
            if p.is_dir():
                p.rmdir()
            elif p.exists():
                p.unlink()
        except OSError:
            pass


def _output_path(output_dir: Path, item: Path, overwrite: bool) -> tuple[Path, str | None, str | None]:
    game = read_folder_game_info(item) if item.is_dir() else None
    title_id = (game.title_id if game else None) or title_id_from_name(item.name)
    title = game.title if game else None
    base = title_id or safe_filename(item.stem if item.is_file() else item.name)
    out = output_dir / f"{base}.ffpfsc"
    if out.exists():
        if overwrite:
            out.unlink()
        else:
            out = unique_path(out)
    return out, title_id, title


def _pack_args(spec: dict[str, Any], cpu: int) -> list[str]:
    args = [
        "--version", "PS5",
        "--inode-bits", "32",
        "--compression-level", str(int(spec.get("level", 9))),
        "--cpu-count", str(cpu),
        "--threshold-gain", "5",
    ]
    block = str(spec.get("block_size") or "auto")
    if block != "auto":
        args += ["--block-size", block]
    if spec.get("temp_dir"):
        args += ["--temp-folder", spec["temp_dir"]]
    if spec.get("verbose"):
        args.append("--verbose")
    return args


def run_compress(spec: dict[str, Any], em: Emitter) -> dict[str, Any]:
    source = Path(spec["source"])
    kind = SourceKind(spec["source_kind"])
    output_dir = Path(spec["output_dir"])
    verify = bool(spec.get("verify"))
    temp_root = Path(spec["temp_dir"]) if spec.get("temp_dir") else None

    stages: list[tuple[str, str, float]] = []
    if kind is SourceKind.ARCHIVE:
        stages.append(("extract", "Extract archive", 30))
    stages += [("prepare", "Prepare", 2), ("build", "Build image", 68 if kind is SourceKind.ARCHIVE else 98)]
    if verify:
        stages.append(("verify", "Verify", 20))
    em.plan(stages)

    output_dir.mkdir(parents=True, exist_ok=True)
    workdir: Path | None = None
    outputs: list[dict[str, Any]] = []
    try:
        if kind is SourceKind.ARCHIVE:
            em.stage("extract", source.name)
            if temp_root:
                temp_root.mkdir(parents=True, exist_ok=True)
            workdir = Path(tempfile.mkdtemp(prefix="ffpfsc-extract-", dir=temp_root))
            em.send(t="artifact", path=str(workdir))
            archives.extract_archive(
                source, workdir, password=spec.get("password"),
                progress=lambda d, t: em.progress("archive", d, t, d),
            )
            items = [p for p in find_sources(workdir) if p.is_dir() or p.suffix.lower() in IMAGE_SUFFIXES]
            if not items:
                raise JobError(f"No PS5 game folder (eboot.bin + sce_sys) or .exfat image found inside {source.name}.")
            em.log("ok", f"Extracted {source.name}: found {len(items)} game(s).")
        else:
            items = [source]

        for index, item in enumerate(items, 1):
            label = f"{item.name} ({index}/{len(items)})" if len(items) > 1 else item.name
            em.stage("prepare", label)
            out, title_id, title = _output_path(output_dir, item, bool(spec.get("overwrite")))
            em.send(t="artifact", path=str(out))
            em.send(t="artifact", path=str(out) + ".tmp")

            injected: list[Path] = []
            if item.is_dir():
                source_bytes, _count = folder_stats(item)
                apr = spec.get("apr", "auto")
                if apr == "on" or (apr == "auto" and is_apr_game(item)):
                    em.log("info", "APR / AMPR title — adding the AMPR emulator files.")
                    ampr_dir = Path(spec["ampr_dir"]) if spec.get("ampr_dir") else None
                    injected = _inject_ampr(item, ampr_dir, em)
            else:
                source_bytes = item.stat().st_size

            cpu = _auto_cpu_cap(source_bytes, int(spec.get("cpu", 0)), em)
            em.stage("build", label)
            kind_args = ["folder"] if item.is_dir() else ["file", "--compress"]
            argv = ["pack", *kind_args, *_pack_args(spec, cpu), str(item), str(out)]
            try:
                code = _run_mkpfs(argv)
                if code != 0 or not out.is_file():
                    raise JobError(f"mkpfs failed (exit code {code}). See the Activity log for details.")
                if verify:
                    # Before AMPR cleanup, so the injected files are part of the comparison.
                    em.stage("verify", label)
                    _verify_output(out, item, em)
            finally:
                if injected and spec.get("ampr_cleanup", True):
                    _remove_paths(injected)

            outputs.append({
                "path": str(out),
                "size": out.stat().st_size,
                "source_size": source_bytes,
                "title_id": title_id,
                "title": title,
            })
            em.log("ok", f"Created {out.name}")
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
    return {"outputs": outputs}


def _verify_output(out: Path, source: Path, em: Emitter) -> None:
    from .image_reader import verify_against

    em.log("info", "Verifying: reading every file back from the image and comparing it with the source…")
    problems = verify_against(out, source, progress=lambda d, t: em.progress("verify", d, t, d))
    for p in problems:
        em.log("error", p)
    if problems:
        more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        raise JobError(f"Verification failed: {problems[0]}{more}")
    em.log("ok", "Verified: every file in the image matches the source.")


# ── Unpack / verify ─────────────────────────────────────────────────────────


def run_unpack(spec: dict[str, Any], em: Emitter) -> dict[str, Any]:
    from .image_reader import extract

    image = Path(spec["image"])
    dest = Path(spec["dest"])
    selectors = spec.get("selectors") or None
    em.plan([("extract", "Extract files", 100)])
    em.stage("extract", image.name)
    created = not dest.exists()
    if created:
        em.send(t="artifact", path=str(dest))
    stats = extract(image, dest, selectors, progress=lambda d, t: em.progress("extract", d, t, d))
    em.log("ok", f"Extracted {stats.files} files ({stats.bytes:,} bytes) to {dest}")
    return {"outputs": [{"path": str(dest), "size": stats.bytes, "files": stats.files}]}


def run_verify(spec: dict[str, Any], em: Emitter) -> dict[str, Any]:
    """Structure check (mkpfs) plus a full decode of every file."""
    from .image_reader import ImageError, decode_all

    image = Path(spec["image"])
    em.plan([("structure", "Check structure", 5), ("decode", "Decode all data", 95)])
    em.stage("structure", image.name)
    if _run_mkpfs(["verify", str(image)]) != 0:
        raise JobError("The image structure is damaged. See the Activity log.")
    em.stage("decode", image.name)
    try:
        stats = decode_all(image, progress=lambda d, t: em.progress("verify", d, t, d))
    except ImageError as exc:
        raise JobError(str(exc)) from exc
    em.log("ok", f"{image.name}: structure OK; all {stats.files:,} files ({stats.bytes:,} bytes) decoded and "
                 "every compressed block passed its checksum.")
    return {"outputs": []}


JOBS = {"compress": run_compress, "unpack": run_unpack, "verify": run_verify}


def worker_main() -> int:
    if os.name == "posix":
        # Own process group, so Cancel can kill mkpfs's pool workers too.
        try:
            os.setsid()
        except OSError:
            pass
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)  # type: ignore[union-attr]
    os.environ.setdefault("MKPFS_NO_COLOR", "1")
    os.environ.setdefault("MKPFS_NO_UTF8", "1")

    em = Emitter()
    try:
        spec = json.loads(sys.stdin.readline())
        _install_mkpfs_hooks(em)
        result = JOBS[spec["kind"]](spec, em)
        em.send(t="result", ok=True, error=None, oom=False, **result)
        return 0
    except MemoryError:
        em.send(t="result", ok=False, oom=True, outputs=[],
                error="Ran out of memory while compressing.")
        return 1
    except (JobError, archives.ArchiveError) as exc:
        em.send(t="result", ok=False, oom=False, outputs=[], error=str(exc),
                password=isinstance(exc, archives.PasswordRequired))
        return 1
    except Exception as exc:
        traceback.print_exc()
        oom = "MemoryError" in traceback.format_exc()
        em.send(t="result", ok=False, oom=oom, outputs=[], error=f"{type(exc).__name__}: {exc}")
        return 1
