# After Effects Render Manager

A Python-based background daemon that automates the organization of After Effects renders. It monitors a temporary output directory and uses a local LLM (Ollama) to intelligently match and move files into a structured project hierarchy.

## Features

- **Automated Monitoring:** Uses Watchdog to detect new files in the render output folder.
- **LLM-Powered Sorting:** Employs Ollama (qwen3.5:4b) for fuzzy matching of folder names, handling typos and abbreviations (e.g., matching "wrk" to "Work").
- **Structured Hierarchy:** Automatically sorts renders into: `[Root] / [WorkType] / [BrandName] / [ProjectID] / passes / afterEffects /`.
- **Smart Sharing:** Copies a renamed version of the render to a `toSend` directory (`brandName_fileName.ext`) for quick distribution.
- **Conflict Resolution:** Incremental versioning for both project files (`_v01`) and shared files (`_1`, `_2`) to prevent overwriting.
- **Auto-Cleanup:** Daily routine to delete files older than 14 days from the source and `toSend` directories.
- **System Integration:** Runs as a systemd user service with desktop notifications via `notify-send`.

## Prerequisites

- Python 3.x
- **Ollama:** A local LLM runner.
  - [Download and install Ollama](https://ollama.com/download).
  - Pull the required model:
    ```bash
    ollama pull qwen:4b  # The project is configured for qwen3.5:4b or qwen:4b equivalents
    ```
- `libnotify-bin` (for desktop notifications)

## Installation

1. Clone the repository to your local machine.
2. Run the installation script:
   ```bash
   ./install.sh
   ```
   This script sets up a virtual environment, installs dependencies, and configures the systemd user service.

## Configuration

Configuration is stored in `~/.config/aeRenderManager/config.json`. You can modify the following paths:

- `sourceDir`: The temporary folder where After Effects saves renders.
- `baseWorkDir`: The root directory for your project hierarchy.
- `ollamaModel`: The local model used for matching (default: `qwen3.5:4b`).
- `cleanupDays`: Retention period for temporary and shared files (default: 14).

## File Naming Convention

For the daemon to sort files correctly, renders must follow this naming pattern:
`fileName_workType_brandName_projectId.ext`

Example: `finalComp_freelance_wishAndKey_12.mp4` (Where `12` is the `projectId` that matches a folder like `12_mothersDay`)

## Service Management

Check service status:
```bash
systemctl --user status ae-render-manager.service
```

Restart service:
```bash
systemctl --user restart ae-render-manager.service
```

View logs:
```bash
tail -f ~/.local/state/aeRenderManager/daemon.log
```
