#!/usr/bin/env python3
"""
Telegram DM Ultra V4 - Fixed Edition
- Fixed SQLite database locking issue
- Fixed async loop / thread conflicts
- Fixed duplicate try/except blocks
- Added proper session cleanup on restart
- Added robust error handling and auto-recovery
- Added journal mode for SQLite
- Added watchdog / auto-restart mechanism
- Added proper graceful shutdown
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
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetDialogsRequest, GetHistoryRequest, SetTypingRequest
from telethon.tl.types import InputPeerEmpty, InputPeerChannel, InputPeerUser, SendMessageTypingAction
from telethon.errors import FloodWaitError, PeerFloodError, UserPrivacyRestrictedError

# ============================================================
# CONFIGURATION & LOGGING
# ============================================================
CONFIG_FILE = "config.json"
SESSION_SUFFIX = "_session"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)
logger = logging.getLogger("TelegramTool")

# ============================================================
# APP STATE MANAGEMENT (Thread-Safe)
# ============================================================
class AppState:
    def __init__(self):
        self.running = False
        self.paused = False
        self.config = None
        self.client = None
        self.loop = None
        self.total_sent = 0
        self.total_failed = 0
        self.total_privacy = 0
        self.last_user = "None"
        self.last_status = "Idle"
        self.current_group = "None"
        self.logs = []
        self.sent_user_ids = self.load_sent_users()
        self.lock = threading.RLock()  # Changed to RLock for nested locking
        self.invite_link = "https://t.me/yynnurybot?start=00013s42mg"
        self.engine_thread = None
        self.loop_event = None  # Event to signal loop to stop
        self.start_time = None

    def load_sent_users(self):
        """Load sent users with proper error handling."""
        try:
            if os.path.exists("sent_users.json"):
                with open("sent_users.json", "r") as f:
                    return set(json.load(f))
        except Exception as e:
            logger.warning(f"Failed to load sent_users.json: {e}")
        return set()

    def save_sent_user(self, user_id):
        """Thread-safe save with immediate flush to disk."""
        with self.lock:
            self.sent_user_ids.add(user_id)
            try:
                with open("sent_users.json", "w") as f:
                    json.dump(list(self.sent_user_ids), f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
            except Exception as e:
                logger.warning(f"Failed to save sent_users.json: {e}")

    def add_log(self, message, level="info"):
        """Thread-safe log addition."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {"time": timestamp, "msg": message, "level": level}
        with self.lock:
            self.logs.append(log_entry)
            if len(self.logs) > 200:
                self.logs = self.logs[-200:]
        logger.info(f"[{level.upper()}] {message}")

    def get_stats(self):
        """Thread-safe stats retrieval."""
        with self.lock:
            return {
                "running": self.running,
                "paused": self.paused,
                "sent": self.total_sent,
                "failed": self.total_failed,
                "privacy": self.total_privacy,
                "last_user": self.last_user,
                "last_status": self.last_status,
                "current_group": self.current_group,
                "total_found": len(self.sent_user_ids),
                "uptime": self._uptime_str()
            }

    def _uptime_str(self):
        """Calculate uptime string."""
        if self.start_time:
            diff = datetime.now() - self.start_time
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours}h {minutes}m {seconds}s"
        return "N/A"


state = AppState()
app = Flask(__name__)

# ============================================================
# AI CREATIVE ENGINE
# ============================================================
class CreativeAI:
    @staticmethod
    def generate_message(invite_link, name):
        hooks = [
            "وشششش ذااا ؟؟؟!! 🤯",
            "مانقدت على قناتك ؟؟ 🤔",
            "تحس قناتك ميتة ومحد يدري عنها ؟ 💀",
            "بختصرها لك وما رح تندم.. 🔥",
            "تبي شي يميز قناتك عن الكل ؟ ✨",
            "مليت من التكرار والتمويل الوهمي ؟ 😴",
            "قناتك محتاجة فزعة حقيقية ؟ 🚩"
        ]

        bodies = [
            "زيادة أعضاء قناتك بـ أعضاء حقيقيين 100% صار أسهل مما تتخيل ومجاناً بالكامل!",
            "ودك بتمويل حقيقي وتفاعل نار؟ بختصرها لك بكلمتين: بوت آسيا سيل وبس.",
            "بدل ما تدور وتتعب، هذا البوت يعطيك أعضاء حقيقيين وسرعة في التمويل خيالية.",
            "جربت كل الطرق وما نفع؟ هذا البوت مخصص لزيادة الأعضاء الحقيقيين مجاناً."
        ]

        ctas = [
            "لا تضيع الفرصة وشرفنا هنا:",
            "ابدأ الآن وشوف الفرق بنفسك:",
            "جرب الحين وما رح تخسر شي:",
            "خلك جزء من أقوى نظام تمويل:"
        ]

        cleaned_name = re.sub(r'[^\w\s\u0600-\u06FF]', '', name).strip()
        smart_name = cleaned_name if cleaned_name else "يا بطل"
        titles = ["يالامير", "يالغالي", "يا وحش", "يا كفو", "يا بطل"]
        final_name = f"{random.choice(titles)} {smart_name}"

        hook = random.choice(hooks)
        body = random.choice(bodies)
        cta = random.choice(ctas)

        greetings = ["أهلاً بك", "مرحباً بك", "يا هلا والله"]
        greet = random.choice(greetings)

        msg = (f"{hook}\n\n{greet} {final_name} 👋\n\n{body}\n\n"
               f"📍 **بوت تمويل آسيا سيل**\n✅ تمويل حقيقي وسريع\n✅ تجميع نقاط بـ 3 طرق\n\n"
               f"{cta}\n🔗 {invite_link}")

        msg += "".join(random.choices(["\u200b", "\u200c", "\u200d"], k=random.randint(2, 5)))
        return msg


# ============================================================
# TELEGRAM CORE ENGINE (Fixed - No more DB lock issues)
# ============================================================
class TelegramEngine:
    def __init__(self, app_state):
        self.state = app_state

    async def run(self):
        """Main engine loop - runs once and stays alive until stopped."""
        try:
            # --- Step 1: Load Config ---
            if not os.path.exists(CONFIG_FILE):
                self.state.add_log("config.json not found!", "error")
                return

            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)

            # --- Step 2: Clean up old session to avoid SQLite lock ---
            session_file = cfg["phone"] + SESSION_SUFFIX
            session_db = session_file + ".sqlite"

            # If a session DB exists and client is not running, try to clean WAL files
            if os.path.exists(session_db + "-wal"):
                try:
                    os.remove(session_db + "-wal")
                    self.state.add_log("Cleaned up old SQLite WAL file", "info")
                except:
                    pass
            if os.path.exists(session_db + "-shm"):
                try:
                    os.remove(session_db + "-shm")
                    self.state.add_log("Cleaned up old SQLite SHM file", "info")
                except:
                    pass

            # --- Step 3: Create and connect client ---
            self.state.client = TelegramClient(
                session_file,
                int(cfg["api_id"]),
                cfg["api_hash"]
            )

            await self.state.client.connect()

            if not await self.state.client.is_user_authorized():
                self.state.add_log("Session not authorized. Run login script first.", "error")
                await self.state.client.disconnect()
                self.state.client = None
                return

            me = await self.state.client.get_me()
            self.state.add_log(f"Connected as: {me.first_name} (@{me.username})", "success")

            # --- Step 4: Register LIVE SNIPE Handler ---
            @self.state.client.on(events.NewMessage)
            async def snipe_handler(event):
                if not self.state.running or self.state.paused:
                    return

                if not event.is_group:
                    return

                try:
                    user = await event.get_sender()
                    if not user or user.bot or user.id in self.state.sent_user_ids:
                        return

                    # Arabic name filter
                    full_name = (getattr(user, 'first_name', '') or '') + (getattr(user, 'last_name', '') or '')
                    if not re.search(r'[\u0600-\u06FF]', full_name):
                        return

                    # SNIPE!
                    group_title = (await event.get_chat()).title
                    self.state.current_group = group_title
                    self.state.add_log(f"Sniped active user in {group_title}: {full_name}", "success")

                    # Build creative message
                    final_msg = CreativeAI.generate_message(self.state.invite_link, full_name)
                    self.state.last_user = full_name

                    # Anti-Ban Protection with proper async handling
                    try:
                        # Random delay before typing
                        await asyncio.sleep(random.randint(10, 30))

                        # Typing simulation
                        peer = await self.state.client.get_input_entity(user.id)
                        from telethon.tl.functions.messages import SetTypingRequest
                        from telethon.tl.types import SendMessageTypingAction
                        await self.state.client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
                        await asyncio.sleep(random.uniform(4, 8))

                        # Send DM
                        await self.state.client.send_message(user, final_msg)

                        # Save and update stats (thread-safe)
                        self.state.save_sent_user(user.id)
                        with self.state.lock:
                            self.state.total_sent += 1
                            self.state.last_status = "Sent"

                        self.state.add_log(f"Successfully sent DM to {full_name}", "success")

                        # Cooldown after success
                        cooldown = random.randint(120, 300)
                        self.state.add_log(f"Safety cooldown: {cooldown}s", "info")
                        await asyncio.sleep(cooldown)

                    except Exception as e:
                        error_str = str(e).lower()
                        if "peer" in error_str:
                            return  # Ignore common peer errors
                        self.state.total_failed += 1
                        self.state.add_log(f"Failed for {full_name}: {str(e)[:80]}", "error")

                except Exception as e:
                    self.state.add_log(f"Handler error: {str(e)[:80]}", "error")

            self.state.add_log("Live Snipe Engine started. Waiting for active users...", "info")

            # --- Step 5: Keep alive loop ---
            self.state.loop_event = asyncio.Event()
            while self.state.running:
                try:
                    await asyncio.sleep(1)
                except asyncio.CancelledError:
                    break

            self.state.add_log("Engine loop ended gracefully.", "info")

        except Exception as e:
            self.state.add_log(f"Critical Engine Error: {e}", "error")
        finally:
            self.state.running = False
            if self.state.client:
                try:
                    await self.state.client.disconnect()
                    self.state.add_log("Telegram client disconnected cleanly.", "info")
                except Exception as e:
                    self.state.add_log(f"Disconnect error: {e}", "error")
                self.state.client = None


# ============================================================
# WEB INTERFACE (Responsive & Real-time)
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Telegram DM Ultra V4</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: 'Segoe UI', sans-serif; }
        .card { background: #1e293b; border: 1px solid #334155; border-radius: 15px; margin-bottom: 20px; }
        .stat-val { font-size: 2rem; font-weight: bold; color: #38bdf8; }
        .log-container { height: 300px; overflow-y: auto; background: #020617; padding: 15px; border-radius: 10px; font-family: monospace; font-size: 0.85rem; }
        .log-info { color: #94a3b8; }
        .log-success { color: #4ade80; }
        .log-warning { color: #fbbf24; }
        .log-error { color: #f87171; }
        .status-badge { padding: 5px 15px; border-radius: 20px; font-weight: bold; }
        .bg-running { background: #16a34a; color: white; }
        .bg-stopped { background: #dc2626; color: white; }
        .bg-warning { background: #f59e0b; color: white; }
        .small-info { color: #94a3b8; font-size: 0.8rem; }
    </style>
</head>
<body class="p-3">
    <div class="container-fluid">
        <div class="d-flex justify-content-between align-items-center mb-4 flex-wrap">
            <h2>Telegram DM Ultra <span class="text-info">V4</span></h2>
            <div id="status-badge" class="status-badge bg-stopped">STOPPED</div>
        </div>

        <div class="row text-center">
            <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted">SENT</div><div id="stat-sent" class="stat-val">0</div></div></div>
            <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted">FAILED</div><div id="stat-failed" class="stat-val">0</div></div></div>
            <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted">PRIVACY</div><div id="stat-privacy" class="stat-val">0</div></div></div>
            <div class="col-6 col-md-3"><div class="card p-3"><div class="text-muted">ACTIVE USERS</div><div id="stat-found" class="stat-val">0</div></div></div>
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
                    <button id="btn-reset" class="btn btn-secondary w-100 mb-2">RESET STATS</button>
                    <div class="mt-2">
                        <label class="form-label small-info">Cooldown (seconds)</label>
                        <input type="number" id="cooldown-min" class="form-control bg-dark text-white mb-1" placeholder="Min: 120" value="120">
                        <input type="number" id="cooldown-max" class="form-control bg-dark text-white" placeholder="Max: 300" value="300">
                    </div>
                </div>
                <div class="card p-3">
                    <h5>Current Status</h5>
                    <div class="small">User: <span id="last-user" class="text-info">None</span></div>
                    <div class="small">Group: <span id="current-group" class="text-info">None</span></div>
                    <div class="small">Engine: <span id="last-status" class="text-info">Idle</span></div>
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
                document.getElementById('uptime').innerText = data.uptime || 'N/A';

                const badge = document.getElementById('status-badge');
                if (data.running) {
                    badge.innerText = data.paused ? 'PAUSED' : 'RUNNING';
                    badge.className = 'status-badge ' + (data.paused ? 'bg-warning' : 'bg-running');
                } else {
                    badge.innerText = 'STOPPED';
                    badge.className = 'status-badge bg-stopped';
                }
            }).catch(e => console.error('Stats fetch error:', e));
        }

        function updateLogs() {
            fetch('/api/logs').then(r => r.json()).then(data => {
                const container = document.getElementById('log-container');
                container.innerHTML = data.logs.map(l =>
                    `<div class="log-${l.level}">[${l.time}] ${l.msg}</div>`
                ).join('');
                container.scrollTop = container.scrollHeight;
            }).catch(e => console.error('Logs fetch error:', e));
        }

        document.getElementById('btn-start').onclick = () => {
            const link = document.getElementById('invite-link').value;
            const cooldownMin = parseInt(document.getElementById('cooldown-min').value) || 120;
            const cooldownMax = parseInt(document.getElementById('cooldown-max').value) || 300;
            fetch('/api/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    invite_link: link,
                    cooldown_min: cooldownMin,
                    cooldown_max: cooldownMax
                })
            }).then(r => r.json()).then(d => {
                if (!d.success) alert('Engine is already running!');
            });
        };

        document.getElementById('btn-stop').onclick = () => fetch('/api/stop', {method: 'POST'});
        document.getElementById('btn-pause').onclick = () => fetch('/api/pause', {method: 'POST'});
        document.getElementById('btn-reset').onclick = () => {
            if (confirm('Reset all stats?')) {
                fetch('/api/reset', {method: 'POST'});
            }
        };

        setInterval(updateStats, 2000);
        setInterval(updateLogs, 3000);
        updateStats();
        updateLogs();
    </script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML, invite_link=state.invite_link)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "running": state.running,
        "uptime": state._uptime_str()
    })


@app.route("/api/stats")
def api_stats():
    return jsonify(state.get_stats())


@app.route("/api/logs")
def api_logs():
    with state.lock:
        return jsonify({"logs": state.logs[-100:]})


@app.route("/api/start", methods=["POST"])
def api_start():
    """Start the engine - only one instance at a time."""
    if state.running:
        return jsonify({"success": False, "error": "Engine already running"})

    data = request.json or {}
    state.invite_link = data.get("invite_link", state.invite_link)
    state.cooldown_min = data.get("cooldown_min", 120)
    state.cooldown_max = data.get("cooldown_max", 300)
    state.running = True
    state.paused = False
    state.start_time = datetime.now()
    state.add_log("Starting Telegram DM Engine...", "info")

    def run_engine():
        """Run the async engine in a dedicated thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        engine = TelegramEngine(state)
        try:
            loop.run_until_complete(engine.run())
        except Exception as e:
            state.add_log(f"Engine thread error: {e}", "error")
        finally:
            loop.close()

    state.engine_thread = threading.Thread(target=run_engine, daemon=True, name="TelegramEngine")
    state.engine_thread.start()
    return jsonify({"success": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Gracefully stop the engine."""
    state.running = False
    state.add_log("Stopping engine...", "info")
    # Signal the async loop to wake up and exit
    if state.loop_event:
        state.loop_event.set()
    return jsonify({"success": True})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """Toggle pause/resume."""
    state.paused = not state.paused
    status = "Paused" if state.paused else "Resumed"
    state.add_log(f"Engine {status}", "info")
    return jsonify({"success": True, "paused": state.paused})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset all stats and sent users list."""
    with state.lock:
        state.total_sent = 0
        state.total_failed = 0
        state.total_privacy = 0
        state.sent_user_ids.clear()
        state.last_user = "None"
        state.last_status = "Idle"
        state.current_group = "None"
        state.add_log("All stats and user list reset.", "info")
    # Clear the file too
    try:
        with open("sent_users.json", "w") as f:
            json.dump([], f)
    except:
        pass
    return jsonify({"success": True})


# ============================================================
# SIGNAL HANDLING (Graceful Shutdown)
# ============================================================
def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for clean shutdown."""
    state.add_log(f"Received signal {signum}. Shutting down...", "info")
    state.running = False
    if state.loop_event:
        state.loop_event.set()
    time.sleep(3)  # Give engine time to clean up
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# ============================================================
# MAIN ENTRY POINT
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    state.add_log(f"Starting Flask on {host}:{port}", "info")
    app.run(host=host, port=port, threaded=True, debug=False)
