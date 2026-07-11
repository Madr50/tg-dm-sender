#!/usr/bin/env python3
"""
Telegram DM Invite Link Sender - Flask Web Version
===================================================
Runs continuously as a web service.
Features:
  - Flask API + Web Dashboard
  - Uptime Robot compatible (/health endpoint)
  - Background DM sending with threading
  - Start/Stop/Pause controls via web
  - Real-time stats via API
  - Persistent state across restarts

Usage:
  pip install -r requirements.txt
  python app.py
"""

import asyncio
import csv
import json
import logging
import os
import random
import sys
import threading
import time
from datetime import datetime
from queue import Queue, Empty

import telethon
from telethon import TelegramClient
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, InputPeerChannel, InputPeerUser
from telethon.errors.rpcerrorlist import (
    PeerFloodError,
    FloodWaitError,
    UserPrivacyRestrictedError,
)

from flask import Flask, jsonify, request, render_template_string, Response

# ============================================================
# CONFIGURATION
# ============================================================
PORT = int(os.environ.get("PORT", 5000))
HOST = "0.0.0.0"

DEFAULT_MIN_DELAY = 15
DEFAULT_MAX_DELAY = 45
DEFAULT_BATCH_SIZE = 10
DEFAULT_BATCH_BREAK = 120
DEFAULT_FLOOD_MULTIPLIER = 1.5
DEFAULT_USERS_PER_GROUP = 15
DEFAULT_GROUPS_LIMIT = 10
MAX_CONSECUTIVE_FLOODS = 3

# Paths
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"
LOG_FILE = "tool.log"
SENT_USERS_FILE = "sent_users.csv"

# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("TgDM")


# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__)


# ============================================================
# STATE & STATS (thread-safe)
# ============================================================
class AppState:
    def __init__(self):
        self.lock = threading.Lock()
        self.config = None
        self.client = None
        self.running = False
        self.paused = False
        self.loop_thread = None
        self.loop_event = threading.Event()

        # Stats
        self.total_sent = 0
        self.total_failed = 0
        self.total_privacy = 0
        self.total_users_found = 0
        self.current_group = ""
        self.last_user = ""
        self.last_message_status = ""
        self.last_flood_time = None
        self.consecutive_floods = 0
        self.start_time = None
        self.invite_link = ""
        self.message_template = ""
        self.log_entries = Queue(maxsize=200)

        # Resume state
        self.sent_user_ids = set()
        self.load_sent_users()

    def load_sent_users(self):
        if os.path.exists(SENT_USERS_FILE):
            with open(SENT_USERS_FILE, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row:
                        try:
                            self.sent_user_ids.add(int(row[0]))
                        except ValueError:
                            pass

    def add_sent_user(self, user_id, name):
        self.sent_user_ids.add(user_id)
        with open(SENT_USERS_FILE, "a", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([user_id, name, datetime.now().isoformat()])

    def add_log(self, message, level="info"):
        try:
            self.log_entries.put_nowait({
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": message,
                "level": level,
            })
        except Exception:
            pass  # Queue full

    def get_logs(self, limit=50):
        logs = []
        while len(logs) < limit and not self.log_entries.empty():
            logs.append(self.log_entries.get())
        return logs

    def get_stats(self):
        with self.lock:
            uptime = 0
            if self.start_time:
                uptime = int((time.time() - self.start_time))
            return {
                "running": self.running,
                "paused": self.paused,
                "uptime": uptime,
                "total_sent": self.total_sent,
                "total_failed": self.total_failed,
                "total_privacy": self.total_privacy,
                "total_users_found": self.total_users_found,
                "current_group": self.current_group,
                "last_user": self.last_user,
                "last_status": self.last_message_status,
                "consecutive_floods": self.consecutive_floods,
                "invite_link": self.invite_link,
                "start_time": self.start_time,
            }

    def reset_stats(self):
        with self.lock:
            self.total_sent = 0
            self.total_failed = 0
            self.total_privacy = 0
            self.total_users_found = 0
            self.consecutive_floods = 0
            self.start_time = time.time()


state = AppState()


# ============================================================
# HTML DASHBOARD TEMPLATE
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram DM Sender</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: #0f0f23;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
        }
        h1 {
            text-align: center;
            color: #00d4ff;
            font-size: 1.8em;
            margin-bottom: 5px;
        }
        .subtitle {
            text-align: center;
            color: #888;
            margin-bottom: 25px;
            font-size: 0.9em;
        }
        .status-bar {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-bottom: 20px;
        }
        .status-badge {
            padding: 6px 20px;
            border-radius: 20px;
            font-weight: bold;
            font-size: 0.85em;
        }
        .status-running { background: #00c853; color: #000; }
        .status-paused { background: #ff9100; color: #000; }
        .status-stopped { background: #f44336; color: #fff; }

        .card {
            background: #1a1a2e;
            border: 1px solid #333;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 15px;
        }
        .card h3 {
            color: #00d4ff;
            margin-bottom: 15px;
            font-size: 1.1em;
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
        }
        .stat-box {
            background: #16213e;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
        }
        .stat-box .number {
            font-size: 2em;
            font-weight: bold;
            color: #00d4ff;
        }
        .stat-box .label {
            font-size: 0.8em;
            color: #888;
            margin-top: 5px;
        }
        .stat-box.sent .number { color: #00c853; }
        .stat-box.failed .number { color: #f44336; }
        .stat-box.privacy .number { color: #ff9100; }

        .btn-group {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 10px 25px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: bold;
            font-size: 0.95em;
            transition: all 0.3s;
        }
        .btn:hover { transform: translateY(-2px); opacity: 0.9; }
        .btn-start { background: #00c853; color: #000; }
        .btn-stop { background: #f44336; color: #fff; }
        .btn-pause { background: #ff9100; color: #000; }
        .btn-reset { background: #666; color: #fff; }

        .form-group {
            margin-bottom: 15px;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            color: #aaa;
            font-size: 0.9em;
        }
        .form-group input, .form-group textarea {
            width: 100%;
            padding: 10px;
            background: #16213e;
            border: 1px solid #333;
            border-radius: 8px;
            color: #e0e0e0;
            font-size: 0.95em;
        }
        .form-group textarea {
            min-height: 80px;
            resize: vertical;
        }

        .log-area {
            background: #0d1117;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 15px;
            max-height: 400px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.82em;
            line-height: 1.6;
        }
        .log-entry { padding: 2px 0; }
        .log-info { color: #58a6ff; }
        .log-success { color: #3fb950; }
        .log-warning { color: #d29922; }
        .log-error { color: #f85149; }

        .uptime {
            text-align: center;
            color: #888;
            font-size: 0.85em;
            margin-top: 5px;
        }

        .refresh-note {
            text-align: center;
            color: #555;
            font-size: 0.75em;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Telegram DM Invite Sender</h1>
        <p class="subtitle">Automated invite link sender with anti-ban protection</p>

        <div class="status-bar" id="statusBar">
            <span class="status-badge status-stopped" id="statusBadge">STOPPED</span>
        </div>

        <div class="card">
            <h3>Configuration</h3>
            <div class="form-group">
                <label>Invite Link</label>
                <input type="text" id="inviteLink" placeholder="https://t.me/bot?start=code" value="{{ invite_link or '' }}">
            </div>
            <div class="form-group">
                <label>Message Template (use {name} for user's name)</label>
                <textarea id="messageTemplate" placeholder="Hey {name}! Check this out...">{{ message_template or '' }}</textarea>
            </div>
            <div class="btn-group">
                <button class="btn btn-start" onclick="startCampaign()">Start</button>
                <button class="btn btn-pause" onclick="pauseCampaign()">Pause</button>
                <button class="btn btn-stop" onclick="stopCampaign()">Stop</button>
                <button class="btn btn-reset" onclick="resetStats()">Reset Stats</button>
            </div>
        </div>

        <div class="card">
            <h3>Statistics</h3>
            <div class="stats-grid">
                <div class="stat-box sent">
                    <div class="number" id="statSent">0</div>
                    <div class="label">Sent</div>
                </div>
                <div class="stat-box privacy">
                    <div class="number" id="statPrivacy">0</div>
                    <div class="label">Privacy Skip</div>
                </div>
                <div class="stat-box failed">
                    <div class="number" id="statFailed">0</div>
                    <div class="label">Failed</div>
                </div>
                <div class="stat-box">
                    <div class="number" id="statFlood">0</div>
                    <div class="label">Flood Errors</div>
                </div>
            </div>
            <p class="uptime" id="uptime">Uptime: 0s</p>
            <p class="uptime" id="lastUser"></p>
        </div>

        <div class="card">
            <h3>Activity Log</h3>
            <div class="log-area" id="logArea"></div>
        </div>

        <p class="refresh-note">Dashboard auto-refreshes every 5 seconds</p>
    </div>

    <script>
        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                document.getElementById('statSent').textContent = data.total_sent;
                document.getElementById('statPrivacy').textContent = data.total_privacy;
                document.getElementById('statFailed').textContent = data.total_failed;
                document.getElementById('statFlood').textContent = data.consecutive_floods;

                const badge = document.getElementById('statusBadge');
                if (data.running && !data.paused) {
                    badge.textContent = 'RUNNING';
                    badge.className = 'status-badge status-running';
                } else if (data.paused) {
                    badge.textContent = 'PAUSED';
                    badge.className = 'status-badge status-paused';
                } else {
                    badge.textContent = 'STOPPED';
                    badge.className = 'status-badge status-stopped';
                }

                const hrs = Math.floor(data.uptime / 3600);
                const mins = Math.floor((data.uptime % 3600) / 60);
                const secs = data.uptime % 60;
                document.getElementById('uptime').textContent =
                    'Uptime: ' + hrs + 'h ' + mins + 'm ' + secs + 's';

                if (data.last_user) {
                    document.getElementById('lastUser').textContent =
                        'Last: ' + data.last_status + ' to ' + data.last_user;
                }
            } catch(e) { console.error(e); }
        }

        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                const logArea = document.getElementById('logArea');
                logArea.innerHTML = '';
                data.logs.forEach(function(entry) {
                    const cls = entry.level === 'success' ? 'log-success' :
                                entry.level === 'warning' ? 'log-warning' :
                                entry.level === 'error' ? 'log-error' : 'log-info';
                    logArea.innerHTML += '<div class="log-entry ' + cls + '">[' +
                        entry.time + '] ' + entry.message + '</div>';
                });
                logArea.scrollTop = logArea.scrollHeight;
            } catch(e) { console.error(e); }
        }

        async function startCampaign() {
            const link = document.getElementById('inviteLink').value;
            const msg = document.getElementById('messageTemplate').value;
            try {
                const res = await fetch('/api/start', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({invite_link: link, message: msg})
                });
                const data = await res.json();
                if (data.success) {
                    alert('Campaign started!');
                } else {
                    alert('Error: ' + data.error);
                }
            } catch(e) { alert('Connection error'); }
        }

        async function pauseCampaign() {
            await fetch('/api/pause', {method: 'POST'});
            alert('Campaign paused');
        }

        async function stopCampaign() {
            await fetch('/api/stop', {method: 'POST'});
            alert('Campaign stopped');
        }

        async function resetStats() {
            await fetch('/api/reset', {method: 'POST'});
            alert('Stats reset');
        }

        // Auto-refresh every 5 seconds
        setInterval(fetchStats, 5000);
        setInterval(fetchLogs, 5000);
        fetchStats();
        fetchLogs();
    </script>
</body>
</html>
"""


# ============================================================
# TELEGRAM SENDER ENGINE
# ============================================================
class TelegramSenderEngine:
    """Background engine that handles all Telegram operations."""

    def __init__(self, app_state):
        self.state = app_state

    async def connect_telegram(self):
        """Connect to Telegram using saved config."""
        if self.state.client and self.state.client.is_connected():
            return True

        if not self.state.config:
            if not os.path.exists(CONFIG_FILE):
                return False
            with open(CONFIG_FILE, "r") as f:
                self.state.config = json.load(f)

        cfg = self.state.config
        client = TelegramClient(
            cfg["phone"] + "_session",
            int(cfg["api_id"]),
            cfg["api_hash"]
        )
        await client.connect()

        if not await client.is_user_authorized():
            self.state.add_log("Not authorized - need login code", "warning")
            # We can't interactively get code, so fail
            await client.disconnect()
            return False

        self.state.client = client
        me = await client.get_me()
        self.state.add_log(f"Connected as: {me.first_name}", "success")
        return True

    async def get_my_groups(self):
        """Fetch all groups the user is a member of."""
        self.state.add_log("Fetching groups...", "info")

        chats = []
        result = await self.state.client(GetDialogsRequest(
            offset_date=None,
            offset_id=0,
            offset_peer=InputPeerEmpty(),
            limit=200,
            hash=0
        ))
        chats.extend(result.chats)

        groups = []
        for chat in chats:
            try:
                if chat.megagroup or chat.gigagroup:
                    groups.append(chat)
            except Exception:
                continue

        self.state.add_log(f"Found {len(groups)} groups", "success")
        return groups

    async def scrape_random_users(self, groups, num_per_group=15, max_groups=10):
        """Scrape random users from random groups."""
        selected_groups = random.sample(groups, min(max_groups, len(groups)))
        all_users = []

        for group in selected_groups:
            try:
                entity = InputPeerChannel(group.id, group.access_hash)
                self.state.current_group = group.title
                participants = await self.state.client.get_participants(entity, limit=100)

                for p in participants:
                    if hasattr(p, 'bot') and p.bot:
                        continue
                    user = {
                        'id': p.id,
                        'access_hash': p.access_hash,
                        'username': getattr(p, 'username', None),
                        'name': (getattr(p, 'first_name', '') or '') + ' ' + (getattr(p, 'last_name', '') or ''),
                    }
                    all_users.append(user)

                self.state.add_log(f"Scraped {len(participants)} from {group.title}", "info")

            except FloodWaitError as e:
                self.state.add_log(f"Flood wait {e.seconds}s on scrape", "warning")
                await asyncio.sleep(e.seconds + 5)
            except Exception as e:
                self.state.add_log(f"Error scraping {group.title}: {e}", "error")

        # Deduplicate
        seen = set()
        unique = []
        for u in all_users:
            if u['id'] not in seen and u['id'] not in self.state.sent_user_ids:
                seen.add(u['id'])
                unique.append(u)

        random.shuffle(unique)
        self.state.total_users_found = len(unique)
        return unique

    async def send_dm(self, user, message):
        """Send a DM and return status."""
        try:
            receiver = InputPeerUser(user['id'], user['access_hash'])
            await self.state.client.send_message(receiver, message)
            return "sent"

        except PeerFloodError:
            return "flood"

        except FloodWaitError as e:
            self.state.add_log(f"FloodWaitError: {e.seconds}s", "error")
            self.state.last_flood_time = time.time()
            await asyncio.sleep(e.seconds + 5)
            return "flood"

        except UserPrivacyRestrictedError:
            return "privacy"

        except ValueError:
            return "unreachable"

        except Exception as e:
            self.state.add_log(f"Error: {e}", "error")
            return "error"

    async def run_campaign(self):
        """Main continuous campaign loop."""
        try:
            # Connect
            connected = await self.connect_telegram()
            if not connected:
                self.state.add_log("Cannot connect to Telegram!", "error")
                self.state.running = False
                return

            # Get groups
            groups = await self.get_my_groups()
            if not groups:
                self.state.add_log("No groups found!", "error")
                self.state.running = False
                return

            flood_consecutive = 0

            while self.state.running:
                # Check if paused
                if self.state.paused:
                    await asyncio.sleep(5)
                    continue

                # Scrape users
                users = await self.scrape_random_users(groups)

                if not users:
                    self.state.add_log("No new users found. Waiting 5 min...", "warning")
                    await asyncio.sleep(300)
                    continue

                self.state.add_log(f"Starting send to {len(users)} users", "info")

                for user in users:
                    if not self.state.running or self.state.paused:
                        break

                    # 1. Clean Name (Remove decorations/emojis for natural call)
                    def clean_name(raw_name):
                        import re
                        # Keep only letters, numbers and basic spaces
                        cleaned = re.sub(r'[^\w\s\u0600-\u06FF]', '', raw_name)
                        cleaned = cleaned.strip()
                        return cleaned if cleaned else "صديقي"

                    name = clean_name(user['name'])
                    self.state.last_user = name

                    # 2. Spintax Variation
                    def get_variation(text):
                        import re
                        pattern = re.compile(r'\{([^{}]*\|[^{}]*)\}')
                        while True:
                            match = pattern.search(text)
                            if not match:
                                break
                            options = match.group(1).split('|')
                            text = text.replace(match.group(0), random.choice(options), 1)
                        return text

                    # 3. Free AI Rephrasing (Mocked with intelligent synonym replacement for safety/speed)
                    def ai_rephrase(text):
                        # This simulates an AI rephraser by slightly altering sentence structure
                        # In a real scenario, we could call a free API like DuckDuckGo AI or similar
                        # For stability, we use a dictionary-based variation system here
                        synonyms = {
                            "أهلاً": ["مرحباً", "يا هلا", "تحية طيبة"],
                            "بوت": ["برنامج", "آلي", "تطبيق"],
                            "مجاني": ["بدون مقابل", "مجاناً بالكامل", "هدية"],
                            "حقيقي": ["واقعي", "فعلي", "أصلي"],
                        }
                        for word, repls in synonyms.items():
                            if word in text and random.random() > 0.5:
                                text = text.replace(word, random.choice(repls), 1)
                        return text

                    msg = self.state.message_template.replace("{name}", name)
                    msg = get_variation(msg)
                    msg = ai_rephrase(msg)

                    # 4. Add random invisible fingerprint
                    invisible_chars = ["\u200b", "\u200c", "\u200d"]
                    msg += "".join(random.choices(invisible_chars, k=random.randint(1, 5)))

                    # 5. Typing Simulation (Strong anti-ban feature)
                    try:
                        from telethon.tl.functions.messages import SetTypingRequest
                        from telethon.tl.types import SendMessageTypingAction
                        receiver = InputPeerUser(user['id'], user['access_hash'])
                        await self.state.client(SetTypingRequest(
                            peer=receiver,
                            action=SendMessageTypingAction()
                        ))
                        # Simulate typing time based on message length
                        typing_time = min(len(msg) / 20, 5) 
                        await asyncio.sleep(typing_time)
                    except Exception:
                        pass

                    result = await self.send_dm(user, msg)

                    with self.state.lock:
                        if result == "sent":
                            self.state.total_sent += 1
                            self.state.last_message_status = "Sent"
                            self.state.add_log(f"Sent to: {name}", "success")
                            self.state.add_sent_user(user['id'], name)
                            flood_consecutive = 0

                        elif result == "flood":
                            flood_consecutive += 1
                            self.state.consecutive_floods = flood_consecutive
                            self.state.last_message_status = "Flood Error"
                            self.state.add_log(f"Flood for: {name}", "error")

                            if flood_consecutive >= MAX_CONSECUTIVE_FLOODS:
                                self.state.add_log(
                                    f"{MAX_CONSECUTIVE_FLOODS} consecutive floods! Stopping.",
                                    "error"
                                )
                                self.state.running = False
                                break

                            # Long break on flood
                            break_time = random.randint(120, 300)
                            self.state.add_log(f"Flood break: {break_time}s", "warning")
                            await asyncio.sleep(break_time)
                            continue

                        elif result == "privacy" or result == "unreachable":
                            self.state.total_privacy += 1
                            self.state.last_message_status = "Privacy Skip"
                            self.state.add_log(f"Privacy skip: {name}", "warning")

                        else:
                            self.state.total_failed += 1
                            self.state.last_message_status = "Failed"

                    # Enhanced Adaptive delay with more randomness
                    base_delay = random.uniform(40, 90) # Increased delay for safety
                    # Add jitter
                    jitter = random.uniform(0.8, 1.2)
                    # Scale up after more messages
                    scale = 1.0 + (self.state.total_sent / 20) * 0.2
                    delay = base_delay * scale * jitter
                    self.state.add_log(f"Waiting {int(delay)}s for next message...", "info")
                    await asyncio.sleep(delay)

                    # Batch break
                    if self.state.total_sent % DEFAULT_BATCH_SIZE == 0 and self.state.total_sent > 0:
                        self.state.add_log(f"Batch complete ({self.state.total_sent} sent). Taking break...", "info")
                        await asyncio.sleep(DEFAULT_BATCH_BREAK)

                # Sleep between full scans
                sleep_time = random.randint(300, 600)
                self.state.add_log(f"Scan complete. Next scan in {sleep_time}s", "info")
                await asyncio.sleep(sleep_time)

        except Exception as e:
            self.state.add_log(f"Critical error: {e}", "error")
            logger.exception("Campaign crash")
        finally:
            self.state.running = False
            if self.state.client:
                await self.state.client.disconnect()
            self.state.add_log("Campaign stopped", "warning")


# ============================================================
# FLASK ROUTES
# ============================================================

@app.route("/")
def dashboard():
    return render_template_string(
        DASHBOARD_HTML,
        invite_link=state.invite_link,
        message_template=state.message_template
    )


@app.route("/health")
def health():
    """Uptime Robot compatible health check."""
    return jsonify({"status": "ok", "uptime": int(time.time())})


@app.route("/api/stats")
def api_stats():
    """Get current statistics."""
    return jsonify(state.get_stats())


@app.route("/api/logs")
def api_logs():
    """Get recent log entries."""
    logs = state.get_logs(50)
    return jsonify({"logs": logs})


@app.route("/api/start", methods=["POST"])
def api_start():
    """Start the campaign."""
    if state.running:
        return jsonify({"success": False, "error": "Already running"})

    data = request.get_json() or {}
    state.invite_link = data.get("invite_link", "").strip()
    state.message_template = data.get("message", "").strip()

    if not state.invite_link:
        return jsonify({"success": False, "error": "Invite link required"})

    if not state.message_template:
        state.message_template = (
            f"Hey! Check this out:\n\n{state.invite_link}\n\n"
            f"Join now!"
        )

    # Reset and start
    state.reset_stats()
    state.running = True
    state.paused = False
    state.loop_event.set()

    # Start background loop
    def campaign_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        engine = TelegramSenderEngine(state)
        loop.run_until_complete(engine.run_campaign())

    state.loop_thread = threading.Thread(target=campaign_loop, daemon=True)
    state.loop_thread.start()

    state.add_log(f"Campaign started! Link: {state.invite_link}", "success")
    return jsonify({"success": True, "message": "Campaign started"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop the campaign."""
    state.running = False
    state.loop_event.set()
    state.add_log("Campaign stopped by user", "warning")
    return jsonify({"success": True})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """Pause the campaign."""
    state.paused = not state.paused
    status = "paused" if state.paused else "resumed"
    state.add_log(f"Campaign {status}", "info")
    return jsonify({"success": True, "status": status})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset statistics."""
    state.reset_stats()
    state.add_log("Statistics reset", "info")
    return jsonify({"success": True})


@app.route("/api/config", methods=["POST"])
def api_config():
    """Save API configuration."""
    data = request.get_json() or {}
    cfg = {
        "api_id": data.get("api_id", ""),
        "api_hash": data.get("api_hash", ""),
        "phone": data.get("phone", ""),
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    state.config = cfg
    state.add_log("Config saved", "success")
    return jsonify({"success": True})


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════╗
║  Telegram DM Sender - Flask Web Version     ║
║  Dashboard: http://0.0.0.0:{PORT}           ║
║  Health:  http://0.0.0.0:{PORT}/health     ║
║  Stats:   http://0.0.0.0:{PORT}/api/stats  ║
╚══════════════════════════════════════════════╝
    """)
    app.run(host=HOST, port=PORT, debug=False)
