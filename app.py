#!/usr/bin/env python3
"""
Telegram DM Ultra V5.1 - Anti-Ban Edition + Login System
=========================================================
Features:
  - Live Snipe (active users in groups)
  - Strong Anti-Ban System (adaptive delay, batch break, flood counter)
  - SMS Login System (via Dashboard - no CLI needed)
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
from telethon.errors import PhoneCodeError, PhoneCodeExpiredError, SessionPasswordNeededError

# ============================================================
# CONFIGURATION
# ============================================================
CONFIG_FILE = "config.json"
SESSION_SUFFIX = "_session"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, force=True)
logger = logging.getLogger("TelegramTool")

# Anti-Ban Settings
MAX_CONSECUTIVE_FLOODS = 3
DEFAULT_BATCH_SIZE = 10
DEFAULT_BATCH_BREAK = 120
BASE_DELAY_MIN = 40
BASE_DELAY_MAX = 90
FLOOD_SCALE = 0.2

# ============================================================
# LOGIN STATE
# ============================================================
login_lock = threading.Lock()
login_state = {
    "status": "idle",          # idle | code_sent | verifying | authorized | error
    "message": "",
    "code_hash": None,
    "phone": None,
}

# ============================================================
# APP STATE
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
# LOGIN FUNCTIONS
# ============================================================
def get_client_info():
    """Load config and return client details."""
    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)
    return cfg

async def send_login_code_async():
    """Send SMS code to user's phone."""
    try:
        cfg = get_client_info()
        session_file = cfg["phone"] + SESSION_SUFFIX
        client = TelegramClient(
            session_file, 
            int(cfg["api_id"]), 
            cfg["api_hash"],
            device_model="iPhone 13 Pro",
            system_version="iOS 15.5",
            app_version="8.8.2"
        )
        await client.connect()

        code_hash = await client.send_code_request(cfg["phone"])

        state.client = client  # Save client for code verification
        with login_lock:
            login_state["status"] = "code_sent"
            login_state["message"] = f"Code sent to {cfg['phone']}! Check Telegram or SMS."
            login_state["code_hash"] = code_hash.phone_code_hash
            login_state["phone"] = cfg["phone"]
        return True
    except Exception as e:
        with login_lock:
            login_state["status"] = "error"
            login_state["message"] = f"Failed: {str(e)[:100]}"
        return False

async def verify_code_async(code, password=None):
    """Verify the SMS code and complete login."""
    try:
        if state.client is None:
            with login_lock:
                login_state["status"] = "error"
                login_state["message"] = "No active session. Restart login."
            return False

        await state.client.sign_in(phone=login_state["phone"], code=code)
        me = await state.client.get_me()

        with login_lock:
            login_state["status"] = "authorized"
            login_state["message"] = f"Logged in as: {me.first_name} (@{me.username})"

        state.add_log(f"Login successful! Connected as {me.first_name}", "success")
        state.client = state.client  # Keep client for engine
        return True
    except SessionPasswordNeededError:
        try:
            await state.client.sign_in(password=password)
            me = await state.client.get_me()
            with login_lock:
                login_state["status"] = "authorized"
                login_state["message"] = f"Logged in as: {me.first_name} (@{me.username})"
            state.add_log(f"Login successful (with 2FA)! Connected as {me.first_name}", "success")
            return True
        except Exception as e2:
            with login_lock:
                login_state["status"] = "error"
                login_state["message"] = f"2FA failed: {str(e2)[:100]}"
            return False
    except PhoneCodeExpiredError:
        with login_lock:
            login_state["status"] = "error"
            login_state["message"] = "Code expired. Please request a new code."
        return False
    except PhoneCodeError:
        with login_lock:
            login_state["status"] = "error"
            login_state["message"] = "Invalid code. Try again."
        return False
    except Exception as e:
        with login_lock:
            login_state["status"] = "error"
            login_state["message"] = f"Error: {str(e)[:100]}"
        return False


# ============================================================
# AI CREATIVE ENGINE (Exact Original Content)
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

        msg = f"{hook}\n\n{greet} {final_name} 👋\n\n{body}\n\n📍 **بوت تمويل آسيا سيل**\n✅ تمويل حقيقي وسريع\n✅ تجميع نقاط بـ 3 طرق\n\n{cta}\n🔗 {invite_link}"
        msg += "".join(random.choices(["\u200b", "\u200c", "\u200d"], k=random.randint(2, 5)))
        return msg


# ============================================================
# TELEGRAM CORE ENGINE WITH ANTI-BAN
# ============================================================
class TelegramEngine:
    def __init__(self, app_state):
        self.state = app_state
        self.flood_consecutive = 0

    async def run(self):
        try:
            if not os.path.exists(CONFIG_FILE):
                self.state.add_log("config.json not found!", "error")
                return

            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)

            session_file = cfg["phone"] + SESSION_SUFFIX

            if self.state.client is None:
                # Use mobile device info to bypass PC-based bans
                self.state.client = TelegramClient(
                    session_file,
                    int(cfg["api_id"]),
                    cfg["api_hash"],
                    device_model="iPhone 13 Pro",
                    system_version="iOS 15.5",
                    app_version="8.8.2",
                    lang_code="en",
                    system_lang_code="en-US"
                )

            if not self.state.client.is_connected():
                await self.state.client.connect()

            if not await self.state.client.is_user_authorized():
                self.state.add_log("Not authorized! Please login via dashboard.", "error")
                return

            client = self.state.client
            me = await client.get_me()
            self.state.add_log(f"Connected as: {me.first_name}", "success")

            # LIVE SNIPE HANDLER
            @client.on(events.NewMessage)
            async def handler(event):
                if not self.state.running or self.state.paused:
                    return
                if not event.is_group:
                    return

                try:
                    user = await event.get_sender()
                    if not user or user.bot:
                        return
                    if user.id in self.state.sent_user_ids:
                        return

                    full_name = (getattr(user, 'first_name', '') or '') + (getattr(user, 'last_name', '') or '')
                    if not re.search(r'[\u0600-\u06FF]', full_name):
                        return

                    group_title = (await event.get_chat()).title
                    self.state.current_group = group_title
                    self.state.add_log(f"Sniped active user in {group_title}: {full_name}", "success")

                    final_msg = CreativeAI.generate_message(self.state.invite_link, full_name)
                    self.state.last_user = full_name

                    # Typing Simulation (3-6 seconds)
                    try:
                        peer = await client.get_input_entity(user.id)
                        await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
                        await asyncio.sleep(random.uniform(3, 6))
                    except Exception:
                        pass

                    # Send DM
                    result = await self._send_dm(client, user, final_msg)

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
                                f"{MAX_CONSECUTIVE_FLOODS} consecutive floods! Stopping campaign.", "error"
                            )
                            self.state.running = False
                            return

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

                    # Adaptive Delay
                    base_delay = random.uniform(BASE_DELAY_MIN, BASE_DELAY_MAX)
                    jitter = random.uniform(0.8, 1.2)
                    scale = 1.0 + (self.state.total_sent / 20) * FLOOD_SCALE
                    delay = base_delay * scale * jitter

                    self.state.add_log(f"Waiting {int(delay)}s for next message... (scale={scale:.1f}x)", "info")
                    await asyncio.sleep(delay)

                    # Batch Break
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
                        self.state.running = False
                    break_time = random.randint(120, 300)
                    await asyncio.sleep(break_time)
                except Exception:
                    pass

            self.state.add_log("Engine started. Fetching groups and active members...", "info")

            # SCRAPE MEMBERS FROM GROUPS
            dialogs = await client.get_dialogs()
            groups = [d for d in dialogs if d.is_group]
            self.state.add_log(f"Found {len(groups)} groups. Starting random member rotation...", "info")

            while self.state.running:
                if self.state.paused:
                    await asyncio.sleep(5)
                    continue

                # Pick a random group
                group = random.choice(groups)
                self.state.current_group = group.title
                
                try:
                    # Get random members (limit to 50 per fetch for safety)
                    members = await client.get_participants(group, limit=50)
                    random.shuffle(members)
                    
                    for user in members:
                        if not self.state.running or self.state.paused: break
                        if user.bot or user.id in self.state.sent_user_ids: continue
                        
                        # Only Arabic names for better targeting
                        full_name = (getattr(user, 'first_name', '') or '') + (getattr(user, 'last_name', '') or '')
                        if not re.search(r'[\u0600-\u06FF]', full_name): continue

                        self.state.last_user = full_name
                        self.state.add_log(f"Targeting: {full_name} from {group.title}", "info")
                        
                        # Simulation
                        try:
                            peer = await client.get_input_entity(user.id)
                            await client(SetTypingRequest(peer=peer, action=SendMessageTypingAction()))
                            await asyncio.sleep(random.uniform(5, 10))
                        except: pass

                        final_msg = CreativeAI.generate_message(self.state.invite_link, full_name)
                        result = await self._send_dm(client, user, final_msg)

                        if result == "sent":
                            with self.state.lock:
                                self.state.total_sent += 1
                                self.state.last_status = "Sent"
                                self.state.consecutive_floods = 0
                                self.state.sent_user_ids.add(user.id)
                            self.state.save_sent_user(user.id)
                            self.state.add_log(f"Sent to {full_name}", "success")
                        elif result == "flood":
                            self.state.consecutive_floods += 1
                            self.state.last_status = "Flood Error"
                            self.state.add_log(f"Flood! Waiting {DEFAULT_BATCH_BREAK}s...", "error")
                            await asyncio.sleep(DEFAULT_BATCH_BREAK)
                            if self.state.consecutive_floods >= MAX_CONSECUTIVE_FLOODS:
                                self.state.running = False
                                break
                        
                        # Anti-ban delay
                        delay = random.randint(BASE_DELAY_MIN, BASE_DELAY_MAX)
                        await asyncio.sleep(delay)

                        if self.state.total_sent % DEFAULT_BATCH_SIZE == 0:
                            self.state.add_log(f"Batch break: {DEFAULT_BATCH_BREAK}s", "info")
                            await asyncio.sleep(DEFAULT_BATCH_BREAK)

                except Exception as e:
                    self.state.add_log(f"Error in {group.title}: {str(e)[:50]}", "warning")
                    await asyncio.sleep(10)

                await asyncio.sleep(5)

        except Exception as e:
            self.state.add_log(f"Critical Engine Error: {e}", "error")
        finally:
            self.state.running = False

    async def _send_dm(self, client, user, message):
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
# WEB INTERFACE WITH LOGIN PANEL
# ============================================================
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Telegram DM Ultra V5.1</title>
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
        .bg-authorized { background: #10b981; color: white; }
        .bg-unauthorized { background: #ef4444; color: white; }
        .login-panel { background: #1a1a2e; border: 2px solid #e94560; border-radius: 15px; padding: 20px; margin-bottom: 20px; }
    </style>
</head>
<body class="p-3">
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h2>Telegram DM Ultra <span class="text-info">V5.1</span></h2>
            <div id="status-badge" class="status-badge bg-stopped">STOPPED</div>
        </div>

        <!-- LOGIN PANEL -->
        <div id="login-section" class="login-panel text-center">
            <h4>Telegram Login</h4>
            <p id="login-message" class="text-warning">Not connected. Request a code to login.</p>
            <div id="login-actions">
                <button id="btn-request-code" class="btn btn-success btn-lg mb-2">Request SMS Code</button>
            </div>
            <div id="code-section" style="display:none;" class="mt-3">
                <input type="text" id="code-input" class="form-control bg-dark text-white mb-2 text-center" 
                       placeholder="Enter code from Telegram/SMS" style="font-size:1.5rem; letter-spacing:5px;">
                <input type="password" id="password-input" class="form-control bg-dark text-white mb-2 text-center" 
                       placeholder="2FA Password (if needed)" style="font-size:1rem;">
                <button id="btn-verify-code" class="btn btn-primary btn-lg">Verify Code</button>
                <button id="btn-resend-code" class="btn btn-secondary btn-lg ms-2">Resend Code</button>
            </div>
            <div id="authorized-section" style="display:none;" class="mt-3">
                <p class="text-success" style="font-size:1.2rem;">Connected! You can now start campaign.</p>
                <button id="btn-logout" class="btn btn-danger btn-lg">Logout & Reset Session</button>
            </div>
        </div>

        <!-- STATS -->
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
                    <button id="btn-start" class="btn btn-success w-100 mb-2" disabled>START CAMPAIGN</button>
                    <button id="btn-stop" class="btn btn-danger w-100 mb-2" disabled>STOP</button>
                    <button id="btn-pause" class="btn btn-warning w-100 mb-2" disabled>PAUSE/RESUME</button>
                    <button id="btn-reset" class="btn btn-secondary w-100 mb-2">RESET LIST</button>
                    <button id="btn-logout" class="btn btn-danger w-100 mb-2">LOGOUT & DELETE SESSION</button>
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
        // Auto-refresh login status
        function updateLoginStatus() {
            fetch('/api/login-status').then(r => r.json()).then(data => {
                const msg = document.getElementById('login-message');
                const actions = document.getElementById('login-actions');
                const codeSection = document.getElementById('code-section');
                const authSection = document.getElementById('authorized-section');
                const startBtn = document.getElementById('btn-start');
                const stopBtn = document.getElementById('btn-stop');
                const pauseBtn = document.getElementById('btn-pause');

                if (data.status === 'authorized') {
                    msg.textContent = data.message;
                    msg.className = 'text-success';
                    actions.style.display = 'none';
                    codeSection.style.display = 'none';
                    authSection.style.display = 'block';
                    startBtn.disabled = false;
                    stopBtn.disabled = false;
                    pauseBtn.disabled = false;
                } else if (data.status === 'code_sent') {
                    msg.textContent = data.message;
                    msg.className = 'text-warning';
                    actions.style.display = 'none';
                    codeSection.style.display = 'block';
                    authSection.style.display = 'none';
                } else if (data.status === 'error') {
                    msg.textContent = data.message;
                    msg.className = 'text-danger';
                    actions.style.display = 'block';
                    codeSection.style.display = 'none';
                    authSection.style.display = 'none';
                } else {
                    msg.textContent = 'Not connected. Request a code to login.';
                    msg.className = 'text-warning';
                    actions.style.display = 'block';
                    codeSection.style.display = 'none';
                    authSection.style.display = 'none';
                }
            });
        }

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

        document.getElementById('btn-request-code').onclick = () => {
            fetch('/api/request-code', {method: 'POST'});
        };

        document.getElementById('btn-verify-code').onclick = () => {
            const code = document.getElementById('code-input').value;
            const password = document.getElementById('password-input').value;
            fetch('/api/verify-code', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({code: code, password: password || null})
            });
        };

        document.getElementById('btn-resend-code').onclick = () => {
            fetch('/api/request-code', {method: 'POST'});
        };

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

        document.getElementById('btn-logout').onclick = () => {
            if (confirm('Delete session? You will need to login again.')) {
                fetch('/api/logout', {method: 'POST'});
            }
        };

        setInterval(updateLoginStatus, 3000);
        setInterval(updateStats, 2000);
        setInterval(updateLogs, 2000);
    </script>
</body>
</html>
"""

# ============================================================
# ROUTES
# ============================================================
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

@app.route("/api/login-status")
def api_login_status():
    with login_lock:
        return jsonify(login_state)

@app.route("/api/request-code", methods=["POST"])
def api_request_code():
    """Request SMS code to login."""
    async def run_login():
        await send_login_code_async()
    asyncio.run(run_login())
    with login_lock:
        return jsonify(login_state)

@app.route("/api/verify-code", methods=["POST"])
def api_verify_code():
    """Verify SMS code."""
    data = request.json or {}
    code = data.get("code", "").strip()
    password = data.get("password", "").strip() or None

    async def run_verify():
        await verify_code_async(code, password)
    asyncio.run(run_verify())
    with login_lock:
        return jsonify(login_state)

@app.route("/api/start", methods=["POST"])
def api_start():
    # Check if authorized
    with login_lock:
        if login_state["status"] != "authorized":
            return jsonify({"success": False, "error": "Not authorized. Please login first."})

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

@app.route("/api/logout", methods=["POST"])
def api_logout():
    """Logout and delete session to bypass ban."""
    # Stop engine
    state.running = False

    # Disconnect client
    async def disconnect():
        if state.client and state.client.is_connected():
            try:
                await state.client.disconnect()
            except:
                pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(disconnect())
    loop.close()
    state.client = None

    # Delete session files
    session_files = [f for f in os.listdir(".") if "session" in f.lower() and (f.endswith(".sqlite") or f.endswith(".sqlite-wal") or f.endswith(".sqlite-shm") or f.endswith(".session"))]
    for sf in session_files:
        os.remove(sf)

    # Reset login state
    with login_lock:
        login_state["status"] = "idle"
        login_state["message"] = "Session deleted. Request a new code to login."

    return jsonify({"success": True, "message": "Session deleted. You can now login again."})


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
