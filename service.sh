#!/bin/bash
# Strava Sync Bot - Service Management Script
# Usage: ./service.sh [install|start|stop|restart|status|logs|uninstall]

SERVICE_NAME="strava-bot"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_PATH="$SCRIPT_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() { echo -e "${GREEN}[✓]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_info() { echo -e "${YELLOW}[i]${NC} $1"; }

install_service() {
    print_info "Installing systemd service..."

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        print_error "Please run with sudo: sudo ./service.sh install"
        exit 1
    fi

    # Check if venv exists
    if [ ! -f "$PYTHON_PATH" ]; then
        print_error "Virtual environment not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        exit 1
    fi

    # Get the user who called sudo
    ACTUAL_USER="${SUDO_USER:-$USER}"

    # Create service file
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=Strava Sync Telegram Bot
After=network.target

[Service]
Type=simple
User=$ACTUAL_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_PATH strava_sync_bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

    # Reload systemd and enable service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME"

    print_status "Service installed and enabled"
    print_info "Run './service.sh start' to start the bot"
}

uninstall_service() {
    if [ "$EUID" -ne 0 ]; then
        print_error "Please run with sudo: sudo ./service.sh uninstall"
        exit 1
    fi

    print_info "Uninstalling service..."
    systemctl stop "$SERVICE_NAME" 2>/dev/null
    systemctl disable "$SERVICE_NAME" 2>/dev/null
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload
    print_status "Service uninstalled"
}

start_service() {
    if [ "$EUID" -ne 0 ]; then
        print_error "Please run with sudo: sudo ./service.sh start"
        exit 1
    fi
    systemctl start "$SERVICE_NAME"
    print_status "Service started"
}

stop_service() {
    if [ "$EUID" -ne 0 ]; then
        print_error "Please run with sudo: sudo ./service.sh stop"
        exit 1
    fi
    systemctl stop "$SERVICE_NAME"
    print_status "Service stopped"
}

restart_service() {
    if [ "$EUID" -ne 0 ]; then
        print_error "Please run with sudo: sudo ./service.sh restart"
        exit 1
    fi
    systemctl restart "$SERVICE_NAME"
    print_status "Service restarted"
}

show_status() {
    systemctl status "$SERVICE_NAME"
}

show_logs() {
    journalctl -u "$SERVICE_NAME" -f --no-pager -n 50
}

show_help() {
    echo "Strava Sync Bot - Service Manager"
    echo ""
    echo "Usage: ./service.sh [command]"
    echo ""
    echo "Commands:"
    echo "  install    - Install and enable the systemd service (requires sudo)"
    echo "  uninstall  - Remove the systemd service (requires sudo)"
    echo "  start      - Start the bot (requires sudo)"
    echo "  stop       - Stop the bot (requires sudo)"
    echo "  restart    - Restart the bot (requires sudo)"
    echo "  status     - Show service status"
    echo "  logs       - Follow live logs (Ctrl+C to exit)"
    echo ""
}

# Main
case "${1:-}" in
    install)   install_service ;;
    uninstall) uninstall_service ;;
    start)     start_service ;;
    stop)      stop_service ;;
    restart)   restart_service ;;
    status)    show_status ;;
    logs)      show_logs ;;
    *)         show_help ;;
esac
