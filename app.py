#!/usr/bin/env python3
"""
Telegram DM Ultra V5.0 - Anti-Ban Edition
==========================================
Features:
  - Live Snipe (active users in groups)
  - Strong Anti-Ban System:
    * Adaptive Delay (starts 40-90s, scales up with each message)
    * Batch Break (120s break every 10 messages)
    * Flood Counter (stops after 3 consecutive floods)
    * Typing Simulation (3-6 seconds)
    * Message variation (invisible fingerprint)
  - Original CreativeAI message content preserved
  - Thread-safe state management
  - Persistent session (no WAL/SHM deletion)
"""

import os
import json
import asyncio
import random
import time
import logging
import threading
import re
import signal
import sys
from datetime import datetime, timedelta
from flask import Flask, render_template_string, jsonify, request

# ============================================================
# TELETHON IMPORTS
# ============================================================
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetDialogsRequest, GetHistoryRequest, SetTypingRequest
from telethon.tl.types import InputPeerEmpty, InputPeerChannel, InputPeerUser, SendMessageTypingAction
from telethon.errors import FloodWaitError, PeerFloodError, UserPrivacyRestrictedError

# ============================================================
# CONFIGURATION
# ============================================================
CONFIG_FILE = "config.json"
SESSION_SUFFIX = "_session"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)
logger = logging.getLogger("TelegramTool")

# Anti-Ban Settings
MAX_CONSECUTIVE_FLOODS = 3       # Stop after 3 consecutive flood errors
DEFAULT_BATCH_SIZE = 10          # Take a break every 10 messages
DEFAULT_BATCH_BREAK = 120        # 120 seconds break between batches
BASE_DELAY_MIN = 40              # Minimum delay between messages
BASE_DELAY_MAX = 90              # Maximum delay between messages
FLOOD_SCALE = 0.2                # How much delay increases per 20 messages
SNIPED_DELAY_MIN = 120           # Minimum delay after sniping
SNIPED_DELAY_MAX = 300           # Maximum delay after sniping

# ============================================================
# APP STATE MANAGEMENT
# ============================================================
class AppState:
    def __init__(self):
        self.running = False
        self.paused = False
        self.config = None
        self.client = None
        self.total_sent = 0
        self.total_failed = 0
        self.total_privacy = 0
        self.consecutive_floods = 0
        self.last_user = "None"
        self.last_status = "Idle"
        self.current_group = "None"
        self.logs = []
        self.sent_user_ids = self.load_sent_users()
        self.lock = threading.RLock()
        self.invite_link = "https://t.me/yynnurybot?start=00013s42mg"
        self.engine_thread = None
        self.start_time = None

    def load_sent_users(self):
        """Load sent users with proper error handling."""
        try:
            if os.path.exists("sent_users.json"):
                with open("sent_users.json", "r") as f:
                    content = f.read().strip()
                    if content:
                        return set(json.loads(content))
        except Exception as e:
            logger.warning(f"Failed to load sent_users.json: {e}")
        return set()

    def save_sent_user(self, user_id):
        """Save user ID to set and persist to disk."""
        try:
            with open("sent_users.json", "w") as f:
                json.dump(list(self.sent_user_ids), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            logger.warning(f"Failed to save sent_users.json: {e}")

    def add_log(self, message, level="info"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {"time": timestamp, "msg": message, "level": level}
        with self.lock:
            self.logs.append(log_entry)
            if len(self.logs) > 100:
                self.logs.pop(0)
        logger.info(f"[{level.upper()}] {message}")

    def get_stats(self):
        with self.lock:
            return {
                "running": self.running,
                "paused": self.paused,
                "sent": self.total_sent,
                "failed": self.total_failed,
                "privacy": self.total_privacy,
                "consecutive_floods": self.consecutive_floods,
                "last_user": self.last_user,
                "last_status": self.last_status,
                "current_group": self.current_group,
                "total_found": len(self.sent_user_ids),
                "uptime": self._uptime_str()
            }

    def _uptime_str(self):
        if self.start_time:
            diff = datetime.now() - self.start_time
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours}h {minutes}m {seconds}s"
        return "N/A"


state = AppState()
app = Flask(__name__)

# ============================================================
# AI CREATIVE ENGINE (Exact Original Content)
# ============================================================
class CreativeAI:
    @staticmethod
    def generate_message(invite_link, name):
        # 1. Dynamic Hooks (Discord Style)
        hooks = [
            "وشششش ذااا ؟؟؟!! 🤯",
            "مانقدت على قناتك ؟؟ 🤔",
            "تحس قناتك ميتة ومحد يدري عنها ؟ 💀",
            "بختصرها لك وما رح تندم.. 🔥",
            "تبي شي يميز قناتك عن الكل ؟ ✨",
            "مليت من التكرار والتمويل الوهمي ؟ 😴",
            "قناتك محتاجة فزعة حقيقية ؟ 🚩"
        ]

        # 2. Body Variations
        bodies = [
            "زيادة أعضاء قناتك بـ أعضاء حقيقيين 100% صار أسهل مما تتخيل ومجاناً بالكامل!",
            "ودك بتمويل حقيقي وتفاعل نار؟ بختصرها لك بكلمتين: بوت آسيا سيل وبس.",
            "بدل ما تدور وتتعب، هذا البوت يعطيك أعضاء حقيقيين وسرعة في التمويل خيالية.",
            "جربت كل الطرق وما نفع؟ هذا البوت مخصص لزيادة الأعضاء الحقيقيين مجاناً."
        ]

        # 3. Call to Action
        ctas = [
            "لا تضيع الفرصة وشرفنا هنا:",
            "ابدأ الآن وشوف الفرق بنفسك:",
            "جرب الحين وما رح تخسر شي:",
            "خلك جزء من أقوى نظام تمويل:"
        ]

        # 4. Smart Name Cleaning
        cleaned_name = re.sub(r'[^\w\s\u0600-\u06FF]', '', name).strip()
        smart_name = cleaned_name if cleaned_name else "يا بطل"
        titles = ["يالامير", "يالغالي", "يا وحش", "يا كفو", "يا بطل"]
        final_name = f"{random.choice(titles)} {smart_name}"

        # 5. Assembly
        hook = random.choice(hooks)
        body = random.choice(bodies)
        cta = random.choice(ctas)

        greetings = ["أهلاً بك", "مرحباً بك", "يا هلا والله"]
        greet = random.choice(greetings)

        msg = f"{hook}\n\n{greet} {final_name} 👋\n\n{body}\n\n📍 **بوت تمويل آسيا سيل**\n✅ تمويل حقيقي وسريع\n✅ تجميع نقاط بـ 3 طرق\n\n{cta}\n🔗 {invite_link}"

        # Add invisible fingerprint (anti-ban)
        msg += "".join(random.choices(["\u200b", "\u200c", "\u200d"], k=random.randint(2, 5)))
        return msg


# ============================================================
# TELEGRAM CORE ENGINE WITH STRONG ANTI-BAN
# ============================================================
class TelegramEngine:
    def __init__(self, app_state):
        self.state = app_state
        self.flood_consecutive = 0

    async def run(self):
        try:
            # 1. Connect (reuse existing session)
            if not os.path.exists(CONFIG_FILE):
                self.state.add_log("config.json not found!", "error")
                return

            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)

            session_file = cfg["phone"] + SESSION_SUFFIX

            if self.state.client is None:
                self.state.client = TelegramClient(
                    session_file,
                    int(cfg["api_id"]),
                    cfg["api_hash"]
                )

            if not self.state.client.is_connected():
                await self.state.client.connect()

            if not await self.state.client.is_user_authorized():
                self.state.add_log("Session not authorized. Run login script first.", "error")
                await self.state.client.disconnect()
                self.state.client = None
                return

            client = self.state.client
            me = await client.get_me()
            self.state.add_log(f"Connected as: {me.first_name}", "success")

            # 2. LIVE SNIPE HANDLER
            @client.on(events.NewMessage)
            async def handler(event):
                if not self.state.running or self.state.paused:
                    return

                # Only target groups
                if not event.is_group:
                    return

                try:
                    user = await event.get_sender()
                    if not user or user.bot:
                        return

                    # Skip already sent users
                    if user.id in self.state.sent_user_ids:
                        return

                    # ARABIC FILTER
                    full_name = (getattr(user, 'first_name', '') or '') + (getattr(user, 'last_name', '') or '')
                    if not re.search(r'[\u0600-\u06FF]', full_name):
                        return

                    # SNIPE!
                    group_title = (await event.get_chat()).title
                    self.state.current_group = group_title
                    self.state.add_log(f"Sniped active user in {group_title}: {full_name}", "success")

                    # Build Creative Message
                    final_msg = CreativeAI.generate_message(self.state.invite_link, full_name)
                    self.state.last_user = full_name

                    # ============================================================
                    # ANTI-BAN PROTECTION
                    # ============================================================

                    # --- Typing Simulation (3-6 seconds) ---
                    try:
                        peer = await client.get_input_entity(user.id)
                        await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
                        await asyncio.sleep(random.uniform(3, 6))
                    except Exception:
                        pass

                    # --- Send DM ---
                    result = await self._send_dm(client, user, final_msg)

                    # --- Handle Result ---
                    if result == "sent":
                        with self.state.lock:
                            self.state.total_sent += 1
                            self.state.last_status = "Sent"
                            self.state.consecutive_floods = 0
                            self.flood_consecutive = 0
                            self.state.sent_user_ids.add(user.id)
                        self.state.save_sent_user(user.id)
                        self.state.add_log(f"Successfully sent to {full_name}", "success")

                    elif result == "flood":
                        self.flood_consecutive += 1
                        with self.state.lock:
                            self.state.consecutive_floods = self.flood_consecutive
                        self.state.last_status = "Flood Error"
                        self.state.add_log(f"Flood for: {full_name}", "error")

                        if self.flood_consecutive >= MAX_CONSECUTIVE_FLOODS:
                            self.state.add_log(
                                f"{MAX_CONSECUTIVE_FLOODS} consecutive floods! Stopping campaign.",
                                "error"
                            )
                            self.state.running = False
                            return

                        # Long break on flood (120-300 seconds)
                        break_time = random.randint(120, 300)
                        self.state.add_log(f"Flood break: {break_time}s...", "warning")
                        await asyncio.sleep(break_time)
                        return

                    elif result == "privacy" or result == "unreachable":
                        self.state.total_privacy += 1
                        self.state.last_status = "Privacy Skip"
                        self.state.add_log(f"Privacy skip: {full_name}", "warning")

                    else:
                        self.state.total_failed += 1
                        self.state.last_status = "Failed"
                        self.state.add_log(f"Failed for {full_name}: unknown error", "error")

                    # --- Adaptive Delay (Anti-Ban Core) ---
                    # Starts at 40-90 seconds, increases after every 20 messages
                    base_delay = random.uniform(BASE_DELAY_MIN, BASE_DELAY_MAX)
                    jitter = random.uniform(0.8, 1.2)
                    scale = 1.0 + (self.state.total_sent / 20) * FLOOD_SCALE
                    delay = base_delay * scale * jitter

                    self.state.add_log(f"Waiting {int(delay)}s for next message... (scale={scale:.1f}x)", "info")
                    await asyncio.sleep(delay)

                    # --- Batch Break ---
                    if self.state.total_sent % DEFAULT_BATCH_SIZE == 0 and self.state.total_sent > 0:
                        self.state.add_log(
                            f"Batch complete ({self.state.total_sent} sent). Taking {DEFAULT_BATCH_BREAK}s break...",
                            "info"
                        )
                        await asyncio.sleep(DEFAULT_BATCH_BREAK)

                except PeerFloodError:
                    self.flood_consecutive += 1
                    self.state.add_log("PeerFloodError - Account restricted!", "error")
                    if self.flood_consecutive >= MAX_CONSECUTIVE_FLOODS:
                        self.state.add_log(
                            f"{MAX_CONSECUTIVE_FLOODS} consecutive floods! Stopping.", "error"
                        )
                        self.state.running = False
                    break_time = random.randint(120, 300)
                    await asyncio.sleep(break_time)
                except Exception as e:
                    pass  # Ignore errors silently to avoid crashing

            self.state.add_log("Live Snipe Engine started. Waiting for active users...", "info")

            # Keep the client running
            while self.state.running:
                await asyncio.sleep(1)

        except Exception as e:
            self.state.add_log(f"Critical Engine Error: {e}", "error")
        finally:
            self.state.running = False
            # DO NOT disconnect - keep session alive

    async def _send_dm(self, client, user, message):
        """Send a DM and return status."""
        try:
            receiver = InputPeerUser(user.id, user.access_hash)
            await client.send_message(receiver, message)
            return "sent"
        except PeerFloodError:
            return "flood"
        except FloodWaitError as e:
            self.state.add_log(f"FloodWaitError: {e.seconds}s", "error")
            await asyncio.sleep(e.seconds + 5)
            return "flood"
        except UserPrivacyRestrictedError:
            return "privacy"
        except ValueError:
            return "unreachable"
        except Exception as e:
            err = str(e).lower()
            if "peer" in err or "user" in err:
                return "unreachable"
            return "flood"


# ============================================================
# WEB INTERFACE
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Telegram DM Ultra V5.0</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 15px; margin-bottom: 20px; }
        .stat-val { font-size: 2rem; font-weight: bold; color: #38bdf8; }
        .log-container { height: 300px; overflow-y: auto; background: #020617; padding: 15px; border-radius: 10px; font-family: monospace; font-size: 0.9rem; }
        .log-info { color: #94a3b8; }
        .log-success { color: #4ade80; }
        .log-warning { color: #fbbf24; }
        .log-error { color: #f87171; }
        .status-badge { padding: 5px 15px; border-radius: 20px; font-weight: bold; }
        .bg-running { background: #16a34a; color: white; }
        .bg-stopped { background: #dc2626; color: white; }
        .bg-warning { background: #f59e0b; color: white; }
    </style>
</head>
<body class="p-3">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h2>Telegram DM Ultra <span class="text-info">V5.0</span></h2>
            <div id="status-badge" class="status-badge bg-stopped">STOPPED</div>
        </div>

        <div class="row text-center">
            <div class="col-md-3"><div class="card p-3"><div class="text-muted">SENT</div><div id="stat-sent" class="stat-val">0</div></div></div>
            <div class="col-md-3"><div class="card p-3"><div class="text-muted">FAILED</div><div id="stat-failed" class="stat-val">0</div></div></div>
            <div class="col-md-3"><div class="card p-3"><div class="text-muted">PRIVACY</div><div id="stat-privacy" class="stat-val">0</div></div></div>
            <div class="col-md-3"><div class="card p-3"><div class="text-muted">ACTIVE USERS</div><div id="stat-found" class="stat-val">0</div></div></div>
        </div>

        <div class="row">
            <div class="col-md-8">
                <div class="card p-3">
                    <h5>Activity Log</h5>
                    <div id="log-container" class="log-container"></div>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card p-3">
                    <h5>Control Panel</h5>
                    <div class="mb-3">
                        <label class="form-label">Invite Link</label>
                        <input type="text" id="invite-link" class="form-control bg-dark text-white" value="{{ invite_link }}">
                    </div>
                    <button id="btn-start" class="btn btn-success w-100 mb-2">START CAMPAIGN</button>
                    <button id="btn-stop" class="btn btn-danger w-100 mb-2">STOP</button>
                    <button id="btn-pause" class="btn btn-warning w-100 mb-2">PAUSE/RESUME</button>
                    <button id="btn-reset" class="btn btn-secondary w-100 mb-2">RESET LIST</button>
                </div>
                <div class="card p-3">
                    <h5>Current Status</h5>
                    <div class="small">User: <span id="last-user" class="text-info">None</span></div>
                    <div class="small">Group: <span id="current-group" class="text-info">None</span></div>
                    <div class="small">Engine: <span id="last-status" class="text-info">Idle</span></div>
                    <div class="small">Flood Errors: <span id="flood-count" class="text-warning">0</span></div>
                    <div class="small">Uptime: <span id="uptime" class="text-info">N/A</span></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function updateStats() {
            fetch('/api/stats').then(r => r.json()).then(data => {
                document.getElementById('stat-sent').innerText = data.sent;
                document.getElementById('stat-failed').innerText = data.failed;
                document.getElementById('stat-privacy').innerText = data.privacy;
                document.getElementById('stat-found').innerText = data.total_found;
                document.getElementById('last-user').innerText = data.last_user;
                document.getElementById('current-group').innerText = data.current_group;
                document.getElementById('last-status').innerText = data.last_status;
                document.getElementById('flood-count').innerText = data.consecutive_floods || 0;
                document.getElementById('uptime').innerText = data.uptime || 'N/A';

                const badge = document.getElementById('status-badge');
                if (data.running) {
                    badge.innerText = data.paused ? 'PAUSED' : 'RUNNING';
                    badge.className = 'status-badge ' + (data.paused ? 'bg-warning' : 'bg-running');
                } else {
                    badge.innerText = 'STOPPED';
                    badge.className = 'status-badge bg-stopped';
                }
            });
        }

        function updateLogs() {
            fetch('/api/logs').then(r => r.json()).then(data => {
                const container = document.getElementById('log-container');
                container.innerHTML = data.logs.map(l =>
                    `<div class="log-${l.level}">[${l.time}] ${l.msg}</div>`
                ).join('');
                container.scrollTop = container.scrollHeight;
            });
        }

        document.getElementById('btn-start').onclick = () => {
            const link = document.getElementById('invite-link').value;
            fetch('/api/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({invite_link: link})
            });
        };

        document.getElementById('btn-stop').onclick = () => fetch('/api/stop', {method: 'POST'});
        document.getElementById('btn-pause').onclick = () => fetch('/api/pause', {method: 'POST'});
        document.getElementById('btn-reset').onclick = () => {
            if (confirm('Clear sent users list? They will receive messages again.')) {
                fetch('/api/reset', {method: 'POST'});
            }
        };

        setInterval(updateStats, 2000);
        setInterval(updateLogs, 2000);
    </script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML, invite_link=state.invite_link)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "running": state.running, "uptime": state._uptime_str()})

@app.route("/api/stats")
def api_stats():
    return jsonify(state.get_stats())

@app.route("/api/logs")
def api_logs():
    return jsonify({"logs": state.logs})

@app.route("/api/start", methods=["POST"])
def api_start():
    if state.running:
        return jsonify({"success": False, "error": "Already running"})
    data = request.json or {}
    state.invite_link = data.get("invite_link", state.invite_link)
    state.running = True
    state.paused = False
    state.start_time = datetime.now()

    def run_engine():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        engine = TelegramEngine(state)
        try:
            loop.run_until_complete(engine.run())
        except Exception as e:
            state.add_log(f"Engine error: {e}", "error")
        finally:
            loop.close()

    state.engine_thread = threading.Thread(target=run_engine, daemon=True)
    state.engine_thread.start()
    return jsonify({"success": True})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    state.running = False
    return jsonify({"success": True})

@app.route("/api/pause", methods=["POST"])
def api_pause():
    state.paused = not state.paused
    return jsonify({"success": True})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    with state.lock:
        state.total_sent = 0
        state.total_failed = 0
        state.total_privacy = 0
        state.consecutive_floods = 0
        state.sent_user_ids.clear()
        state.last_user = "None"
        state.last_status = "Idle"
        state.current_group = "None"
    try:
        with open("sent_users.json", "w") as f:
            json.dump([], f)
    except:
        pass
    return jsonify({"success": True})


# ============================================================
# SIGNAL HANDLING
# ============================================================
def signal_handler(signum, frame):
    state.running = False
    time.sleep(2)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
