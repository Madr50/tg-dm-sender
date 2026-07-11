import os
import json
import asyncio
import random
import time
import logging
import threading
import re
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
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("TelegramTool")

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
        self.last_user = "None"
        self.last_status = "Idle"
        self.current_group = "None"
        self.logs = []
        self.sent_user_ids = set()
        self.lock = threading.Lock()
        self.invite_link = "https://t.me/yynnurybot?start=00013s42mg"
        self.engine_thread = None

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
                "last_user": self.last_user,
                "last_status": self.last_status,
                "current_group": self.current_group,
                "total_found": len(self.sent_user_ids)
            }

state = AppState()
app = Flask(__name__)

# ============================================================
# AI CREATIVE ENGINE (The "Discord Style" Brain)
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
        
        # Add random spintax-like variations
        greetings = ["أهلاً بك", "مرحباً بك", "يا هلا والله"]
        greet = random.choice(greetings)

        msg = f"{hook}\n\n{greet} {final_name} 👋\n\n{body}\n\n📍 **بوت تمويل آسيا سيل**\n✅ تمويل حقيقي وسريع\n✅ تجميع نقاط بـ 3 طرق\n\n{cta}\n🔗 {invite_link}"
        
        # Add invisible fingerprint
        msg += "".join(random.choices(["\u200b", "\u200c", "\u200d"], k=random.randint(2, 5)))
        return msg

# ============================================================
# TELEGRAM CORE ENGINE
# ============================================================
class TelegramEngine:
    def __init__(self, app_state):
        self.state = app_state

    async def run(self):
        try:
            # 1. Connect
            if not os.path.exists(CONFIG_FILE):
                self.state.add_log("config.json not found!", "error")
                return
            
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)

            client = TelegramClient(cfg["phone"] + SESSION_SUFFIX, int(cfg["api_id"]), cfg["api_hash"])
            await client.connect()
            
            if not await client.is_user_authorized():
                self.state.add_log("Session not authorized. Run login script first.", "error")
                await client.disconnect()
                return

            self.state.client = client
            me = await client.get_me()
            self.state.add_log(f"Connected as: {me.first_name}", "success")

            # 2. Main Loop
            while self.state.running:
                if self.state.paused:
                    await asyncio.sleep(5)
                    continue

                # Get Groups
                self.state.add_log("Scanning groups...", "info")
                dialogs = await client(GetDialogsRequest(
                    offset_date=None, offset_id=0, offset_peer=InputPeerEmpty(), limit=100, hash=0
                ))
                groups = [c for c in dialogs.chats if hasattr(c, 'megagroup') and (c.megagroup or c.gigagroup)]
                
                if not groups:
                    self.state.add_log("No groups found!", "warning")
                    await asyncio.sleep(60)
                    continue

                random.shuffle(groups)
                for group in groups:
                    if not self.state.running or self.state.paused: break
                    
                    self.state.current_group = group.title
                    self.state.add_log(f"Targeting active users in: {group.title}", "info")

                    # TARGETING ACTIVE USERS (Scrape from recent history)
                    try:
                        history = await client(GetHistoryRequest(
                            peer=group, offset_id=0, offset_date=None, add_offset=0, limit=50, max_id=0, min_id=0, hash=0
                        ))
                        
                        active_users = []
                        for msg in history.messages:
                            if hasattr(msg, 'from_id') and msg.from_id:
                                try:
                                    user = await client.get_entity(msg.from_id)
                                    if not user.bot and user.id not in self.state.sent_user_ids:
                                        active_users.append(user)
                                except: continue
                        
                        if not active_users:
                            self.state.add_log(f"No new active users in {group.title}", "debug")
                            continue

                        self.state.add_log(f"Found {len(active_users)} active users", "success")
                        
                        for user in active_users:
                            if not self.state.running or self.state.paused: break
                            
                            # Build Creative Message
                            name = (getattr(user, 'first_name', '') or '') + ' ' + (getattr(user, 'last_name', '') or '')
                            final_msg = CreativeAI.generate_message(self.state.invite_link, name)
                            self.state.last_user = name

                            # Anti-Ban Protection
                            try:
                                # Typing Sim
                                peer = await client.get_input_entity(user.id)
                                await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
                                await asyncio.sleep(random.uniform(3, 6))
                                
                                # Send DM
                                await client.send_message(user, final_msg)
                                with self.state.lock:
                                    self.state.total_sent += 1
                                    self.state.last_status = "Sent"
                                    self.state.sent_user_ids.add(user.id)
                                self.state.add_log(f"Successfully sent to {name}", "success")
                                
                            except FloodWaitError as e:
                                self.state.add_log(f"Flood Wait: {e.seconds}s. Stopping for safety.", "error")
                                await asyncio.sleep(e.seconds + 10)
                                break
                            except UserPrivacyRestrictedError:
                                self.state.total_privacy += 1
                                self.state.add_log(f"Privacy skip: {name}", "warning")
                            except Exception as e:
                                self.state.total_failed += 1
                                self.state.add_log(f"Failed for {name}: {str(e)[:50]}", "error")

                            # Safety Delay
                            delay = random.randint(60, 120)
                            self.state.add_log(f"Cooling down: {delay}s...", "info")
                            await asyncio.sleep(delay)

                    except Exception as e:
                        self.state.add_log(f"Error in group {group.title}: {str(e)[:50]}", "debug")
                        continue

                self.state.add_log("Cycle complete. Resting 15 min...", "info")
                await asyncio.sleep(900)

        except Exception as e:
            self.state.add_log(f"Critical Engine Error: {e}", "error")
        finally:
            self.state.running = False
            if self.state.client:
                await self.state.client.disconnect()

# ============================================================
# WEB INTERFACE (Responsive & Real-time)
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Telegram DM Ultra V3</title>
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
    </style>
</head>
<body class="p-3">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h2>🚀 Telegram DM Ultra <span class="text-info">V3</span></h2>
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
                    <button id="btn-pause" class="btn btn-warning w-100">PAUSE/RESUME</button>
                </div>
                <div class="card p-3">
                    <h5>Current Status</h5>
                    <div class="small">User: <span id="last-user" class="text-info">None</span></div>
                    <div class="small">Group: <span id="current-group" class="text-info">None</span></div>
                    <div class="small">Engine: <span id="last-status" class="text-info">Idle</span></div>
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
    return jsonify({"status": "ok", "uptime": int(time.time())})

@app.route("/api/stats")
def api_stats():
    return jsonify(state.get_stats())

@app.route("/api/logs")
def api_logs():
    return jsonify({"logs": state.logs})

@app.route("/api/start", methods=["POST"])
def api_start():
    if state.running: return jsonify({"success": False})
    data = request.json
    state.invite_link = data.get("invite_link", state.invite_link)
    state.running = True
    state.paused = False
    
    def run_engine():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        engine = TelegramEngine(state)
        loop.run_until_complete(engine.run())
        
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
