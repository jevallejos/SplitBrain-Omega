#!/usr/bin/env bash
# ==============================================================================
# INSTALADOR DE PIPELINE CUÁNTICO TRI-SESIÓN (8H: 00:00, 08:00, 16:00 UTC)
# Configura systemd service (Nice=15, best-effort IO) + timer sin tocar el bot en vivo
# ==============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="$(whoami)"
if [ -x "${REPO_DIR}/venv/bin/python" ]; then
    PYTHON_BIN="${REPO_DIR}/venv/bin/python"
else
    PYTHON_BIN="$(command -v python3)"
fi

echo "======================================================================"
echo " ⚙️ CONFIGURANDO PIPELINE CUÁNTICO TRI-SESIÓN (8H) EN: ${REPO_DIR}"
echo " 🐍 Intérprete Python: ${PYTHON_BIN}"
echo "======================================================================"

# 1. Crear unidad systemd de servicio (baja prioridad de CPU/IO para jamás interferir con el motor WebSocket)
sudo tee /etc/systemd/system/splitbrain-eval-24h.service > /dev/null <<EOF
[Unit]
Description=Evaluador Cuantico Tri-Sesion (8h/24h/72h) y Calibrador OOS - SplitBrain-Omega
After=network-online.target

[Service]
Type=oneshot
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${REPO_DIR}/.env
Nice=15
IOSchedulingClass=best-effort
IOSchedulingPriority=7
ExecStart=${PYTHON_BIN} ${REPO_DIR}/7_evaluador_cuantico_24h.py --repo${REPO_DIR} --horas 8 --telegram
StandardOutput=append:${REPO_DIR}/evaluador_24h.log
StandardError=append:${REPO_DIR}/evaluador_24h.log
EOF

# 2. Crear unidad systemd timer (ejecución en los 3 cortes de sesión y Funding Rate: 00:00, 08:00 y 16:00 UTC)
sudo tee /etc/systemd/system/splitbrain-eval-24h.timer > /dev/null <<EOF
[Unit]
Description=Timer Tri-Sesion (00:00, 08:00, 16:00 UTC) para Evaluador Cuantico SplitBrain-Omega

[Timer]
OnCalendar=*-*-* 00,08,16:00:00 UTC
Persistent=true
RandomizedDelaySec=30

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now splitbrain-eval-24h.timer

echo "✅ Timer systemd Tri-Sesión (00:00, 08:00, 16:00 UTC) activado con éxito:"
systemctl status splitbrain-eval-24h.timer --no-pager || true
echo ""
systemctl list-timers splitbrain-eval-24h.timer --no-pager || true
