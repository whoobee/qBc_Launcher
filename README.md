# qBc_Launcher

TUI-based launcher designed to start, monitor, and gracefully shut down all the `qB-Companion` nodes automatically and in the right order.

![Python](https://img.shields.io/badge/Python-3.13-blue) ![Textual](https://img.shields.io/badge/Textual-TUI-green)

## Overview

The launcher uses the `textual` framework to provide a rich Terminal User Interface (TUI). It reads a JSON configuration file detailing all the background services to run, starts them as subprocesses (managing virtual environments if specified), and provides a live, tabbed view of their standard output and status.

## Architecture

- **`main.py`**: Entry point that parses CLI arguments, loads the JSON configuration, and starts the `LauncherTUI`.
- **`launcher.py`**: Contains the `LauncherTUI` textual app.
  - **NodePanel**: A custom Textual widget that displays the node's name, its current status (WAITING, RUNNING, EXITED, FAILED), and a scrolling `RichLog` of its stdout/stderr.
  - **Threaded Execution**: Each process is launched and monitored in its own thread to prevent blocking the UI.
  - **Sequential Startup**: Respects `wait_prev` and `delay_ms` flags in the configuration to ensure services start in the correct order (e.g., Network broker before other nodes).

## Usage

```bash
# Activate the launcher's venv
cd qBc_Launcher
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the launcher with a launch file
python3 main.py launchfiles/default_launch.json
```

### Controls

- `q` or `ESC`: Trigger a graceful shutdown of all monitored child processes and quit the application.
- Mouse or Keyboard: Navigate between tabs (if the number of nodes exceeds `MAX_PANELS_PER_TAB`, which is 6).

## Launch Configuration Format

The configuration is a JSON file containing a list of `scripts`.

```json
{
  "scripts": [
    {
      "name": "Network Broker",
      "script": "../qBc_Network/main.py",
      "venv": "../qBc_Network/.venv"
    },
    {
      "name": "Behavior Service",
      "script": "../qBc_Behavior/behavior_service.py",
      "args": ["--tree", "trees/main_behavior.yaml"],
      "venv": "../qBc_Behavior/.venv",
      "wait_prev": true,
      "delay_ms": 2000
    }
  ]
}
```

### Entry Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | **Required**. Display name in the TUI panel. |
| `script` | string | **Required**. Path to the Python script to execute. |
| `venv` | string | Optional. Path to the virtual environment to activate before running the script. |
| `args` | array[string] | Optional. CLI arguments to pass to the script. |
| `wait_prev` | boolean | Optional. If `true`, waits for the previous script to successfully start before launching this one. |
| `delay_ms` | integer | Optional. Delay in milliseconds before starting this script. |

## Logging

In addition to the TUI output, the launcher automatically writes log files for every session.
Logs are saved in the `qBc_Launcher/logs/<timestamp>/` directory, with one `.log` file per node.
