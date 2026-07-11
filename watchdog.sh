#!/bin/bash
# Watchdog script - Monitors tg-dm-sender and restarts if needed
# Add to crontab: * * * * * /home/ubuntu/tg-dm-sender/watchdog.sh >> /home/ubuntu/tg-dm-sender/watchdog.log 2>&1

APP_DIR="/home/ubuntu/tg-dm-sender"
LOG_FILE="$APP_DIR/watchdog.log"
PORT=5000

# Check if the app is responding
if curl -s --max-time 5 http://localhost:$PORT/health > /dev/null 2>&1; then
    echo "[$(date)] Service is running normally" >> "$LOG_FILE"
else
    echo "[$(date)] Service is DOWN - restarting..." >> "$LOG_FILE"
    # Kill any existing process
    pkill -f "python3 $APP_DIR/app.py" 2>/dev/null
    sleep 3
    # Start the service
    cd "$APP_DIR" && python3 app.py >> "$APP_DIR/app.log" 2>&1 &
    echo "[$(date)] Service restarted" >> "$LOG_FILE"
fi
