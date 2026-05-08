#!/bin/bash

echo "Installing After Effects Render Manager..."

# 1. Create Directories
mkdir -p ~/.config/aeRenderManager
mkdir -p ~/.local/state/aeRenderManager

# 2. Install Python Dependencies
pip install watchdog requests

# 3. Create Systemd User Service
SERVICE_FILE=~/.config/systemd/user/ae-render-manager.service
mkdir -p ~/.config/systemd/user/

cat << EOF > $SERVICE_FILE
[Unit]
Description=After Effects Render Manager Daemon
After=network.target

[Service]
ExecStart=$(which python3) $(pwd)/renderManager.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF

# 4. Reload and Start
systemctl --user daemon-reload
systemctl --user enable ae-render-manager.service
systemctl --user start ae-render-manager.service

echo "------------------------------------------------"
echo "INSTALLATION COMPLETE!"
echo "Service status: $(systemctl --user is-active ae-render-manager.service)"
echo "Config: ~/.config/aeRenderManager/config.json"
echo "Logs:   tail -f ~/.local/state/aeRenderManager/daemon.log"
echo "------------------------------------------------"
echo "IMPORTANT: Edit config.json to set your 'sourceDir' and 'baseWorkDir'."
