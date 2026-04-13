import math
import os
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.widgets import Footer, Header, RichLog, Static, TabbedContent, TabPane
from textual.worker import Worker

STARTUP_GRACE_PERIOD = 2.0
MAX_PANELS_PER_TAB = 6
LOG_DIR = Path(__file__).parent / "logs"


class NodePanel(Static):
    """A panel showing a node's name, status, and scrolling log output."""

    DEFAULT_CSS = """
    NodePanel {
        border: round $accent;
        height: 1fr;
        layout: vertical;
    }
    NodePanel .node-label {
        dock: top;
        height: 1;
        background: $boost;
        text-style: bold;
        padding: 0 1;
    }
    NodePanel RichLog {
        height: 1fr;
    }
    """

    def __init__(self, node_name: str, index: int) -> None:
        super().__init__(id=f"panel-{index}")
        self.node_name = node_name
        self.index = index

    def compose(self) -> ComposeResult:
        yield Static(
            f"{self.node_name} [yellow]WAITING[/]",
            id=f"label-{self.index}",
            classes="node-label",
        )
        yield RichLog(
            id=f"log-{self.index}",
            max_lines=500,
            wrap=True,
            markup=True,
            auto_scroll=True,
        )


class LauncherTUI(App):
    """TUI for launching and monitoring qBc nodes."""

    TITLE = "qBc Launcher"
    BINDINGS = [
        Binding("q", "quit_all", "Quit", show=True),
        Binding("escape", "quit_all", "Quit", show=False),
    ]
    CSS = """
    Grid {
        grid-size: 2 3;
        grid-gutter: 1;
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self, entries: list[dict]) -> None:
        super().__init__()
        for i, entry in enumerate(entries):
            if "name" not in entry:
                raise ValueError(
                    f"Entry {i} ({entry.get('script', '?')}) is missing "
                    f"the required 'name' field in the launch config"
                )
        self._entries = entries
        self._procs: list[subprocess.Popen | None] = [None] * len(entries)
        self._node_names = [e["name"] for e in entries]
        self._shutting_down = threading.Event()
        self._threads: list[threading.Thread] = []
        self._log_files: list = [None] * len(entries)
        timestamp = datetime.now().strftime("%d-%m-%y_%H-%M-%S")
        session_dir = LOG_DIR / timestamp
        session_dir.mkdir(parents=True, exist_ok=True)
        for i, name in enumerate(self._node_names):
            safe_name = name.replace(" ", "-")
            log_path = session_dir / f"{safe_name}.log"
            self._log_files[i] = open(log_path, "w")

    def compose(self) -> ComposeResult:
        yield Header()
        n = len(self._entries)
        num_tabs = math.ceil(n / MAX_PANELS_PER_TAB)

        if num_tabs <= 1:
            # Single page, no tabs needed
            with Grid():
                for i in range(n):
                    yield NodePanel(self._node_names[i], i)
        else:
            with TabbedContent():
                for t in range(num_tabs):
                    start = t * MAX_PANELS_PER_TAB
                    end = min(start + MAX_PANELS_PER_TAB, n)
                    label = f"Nodes {start + 1}-{end}"
                    with TabPane(label, id=f"tab-{t}"):
                        with Grid():
                            for i in range(start, end):
                                yield NodePanel(self._node_names[i], i)
        yield Footer()

    def on_mount(self) -> None:
        self._launch_all()

    def _set_label(self, index: int, status: str) -> None:
        """Update a node's label with a status string."""
        label = self.query_one(f"#label-{index}", Static)
        label.update(f"{self._node_names[index]} {status}")

    def _write_log(self, index: int, text: str) -> None:
        """Write a line to a node's log panel."""
        log = self.query_one(f"#log-{index}", RichLog)
        log.write(text)

    def _start_process(self, entry: dict) -> subprocess.Popen:
        """Start a subprocess.

        Supports three modes via the entry dict:
          - "command" (list[str]): Run a raw command directly (no Python/venv).
          - "script" + "venv": Activate venv, then run Python script.
          - "script" only: Run with current Python interpreter.
        """
        args = entry.get("args", [])

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # Raw command mode (for non-Python binaries like axllm, whisper_srv)
        if "command" in entry:
            cwd = entry.get("cwd", str(Path(__file__).parent))
            resolved_args = []
            for a in args:
                if not a.startswith("-") and (a.startswith("..") or a.startswith(".")):
                    a = str((Path(cwd) / a).resolve())
                resolved_args.append(a)
            cmd = list(entry["command"]) + resolved_args
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )
            return proc

        # Python script mode
        script_path = str(Path(entry["script"]).resolve())
        venv = entry.get("venv", "")
        script_dir = str(Path(script_path).parent)

        if venv:
            activate_path = str((Path(venv).resolve() / "bin" / "activate"))
            quoted_args = " ".join(shlex.quote(a) for a in args)
            shell_cmd = (
                f"source {shlex.quote(activate_path)} && "
                f"exec python {shlex.quote(script_path)} {quoted_args}"
            )
            proc = subprocess.Popen(
                ["bash", "-c", shell_cmd],
                cwd=script_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )
        else:
            cmd = [sys.executable, script_path] + args
            proc = subprocess.Popen(
                cmd,
                cwd=script_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                bufsize=1,
            )
        return proc

    def _read_output(self, index: int, proc: subprocess.Popen) -> None:
        """Read subprocess output line by line and post to the panel and log file."""
        log_file = self._log_files[index]
        try:
            for line in proc.stdout:
                if self._shutting_down.is_set():
                    break
                stripped = line.rstrip("\n")
                self.call_from_thread(self._write_log, index, stripped)
                log_file.write(line)
                log_file.flush()
        except (ValueError, OSError):
            pass  # stdout closed during shutdown
        if self._shutting_down.is_set():
            return
        rc = proc.wait()
        exit_msg = f"Process exited with code {rc}"
        try:
            log_file.write(f"\n{exit_msg}\n")
            log_file.flush()
        except (ValueError, OSError):
            pass
        status = (
            "[green]EXITED (0)[/]" if rc == 0 else f"[red]EXITED ({rc})[/]"
        )
        try:
            self.call_from_thread(self._set_label, index, status)
            self.call_from_thread(
                self._write_log, index, f"\n[bold]{exit_msg}[/bold]"
            )
        except Exception:
            pass  # app may be shutting down

    @property
    def _worker_default_group(self) -> str:
        return "launcher"

    def _launch_all(self) -> None:
        """Coordinator: launch nodes sequentially (respecting wait_prev/delay), read output in parallel."""

        def _coordinator() -> None:
            for i, entry in enumerate(self._entries):
                if self._shutting_down.is_set():
                    return
                wait_prev = entry.get("wait_prev", False)
                delay = entry.get("delay_ms", 0) / 1000.0

                # Check previous process started OK
                if wait_prev and i > 0 and self._procs[i - 1] is not None:
                    time.sleep(STARTUP_GRACE_PERIOD)
                    if self._shutting_down.is_set():
                        return
                    prev = self._procs[i - 1]
                    if prev.poll() is not None:
                        msg = (
                            f"[red]Previous node ({self._node_names[i-1]}) "
                            f"exited with code {prev.returncode}. Aborting.[/red]"
                        )
                        try:
                            self.call_from_thread(self._set_label, i, "[red]ABORTED[/]")
                            self.call_from_thread(self._write_log, i, msg)
                        except Exception:
                            pass
                        return

                if delay > 0:
                    time.sleep(delay)

                try:
                    self.call_from_thread(self._set_label, i, "[yellow]STARTING...[/]")
                    proc = self._start_process(entry)
                    self._procs[i] = proc
                    self.call_from_thread(
                        self._set_label, i, f"[green]RUNNING[/] (PID {proc.pid})"
                    )
                    if "script" in entry:
                        started_name = Path(entry["script"]).name
                    else:
                        started_name = " ".join(entry["command"])
                    self.call_from_thread(
                        self._write_log, i,
                        f"Started {started_name} (PID {proc.pid})"
                    )
                    # Spawn a reader thread for this node's output
                    reader = threading.Thread(
                        target=self._read_output, args=(i, proc),
                    )
                    reader.start()
                    self._threads.append(reader)
                except Exception as exc:
                    try:
                        self.call_from_thread(self._set_label, i, "[red]FAILED[/]")
                        self.call_from_thread(self._write_log, i, f"[red]{exc}[/red]")
                    except Exception:
                        pass
                    continue

        coord = threading.Thread(target=_coordinator, daemon=True)
        coord.start()
        self._threads.append(coord)

    def action_quit_all(self) -> None:
        """Terminate all child processes, wait for threads, and exit."""
        self._shutting_down.set()
        for proc in self._procs:
            if proc is not None and proc.poll() is None:
                proc.terminate()
        for proc in self._procs:
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        for t in self._threads:
            t.join(timeout=3)
        for f in self._log_files:
            if f is not None and not f.closed:
                f.close()
        self.exit()
