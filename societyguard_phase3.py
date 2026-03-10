import hmac
import hashlib
import sqlite3
import datetime
import uuid
import qrcode
import os
from dotenv import load_dotenv
load_dotenv()  # Load secrets from .env file
import fakeredis  # CHANGED: Internal Embedded Redis Service
from fastapi import FastAPI, Request, Form, HTTPException, Response, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
import requests
import io
import numpy as np
import time # Added for timestamps

# Phase 5: Gate Digital Twin AI
try:
    from cv_engine import GateVerifier
    gate_ai_engine = GateVerifier()
except Exception as e:
    print(f"WARNING: Gate AI Engine failed to load: {e}")
    gate_ai_engine = None

# Phase 4: Face Recognition
try:
    import face_recognition
    FACE_REC_AVAILABLE = True
except ImportError:
    FACE_REC_AVAILABLE = False
    print("WARNING: face_recognition not installed. Anomaly detection disabled.")

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET_KEY", "change-me-in-production"))
# Mount current directory for simple video serving (in production use a specific folder)
app.mount("/static_videos", StaticFiles(directory="."), name="static_videos")

# Setup Templates and Static
templates = Jinja2Templates(directory="templates")
os.makedirs("static/qrcodes", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

SECRET = os.environ.get("HMAC_SECRET", "change-me-in-production").encode()
DB_NAME = "iam_society.db"

# Config
# Email (Real SMTP)
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")  # Set in .env
SENDER_PASSWORD = os.environ.get("SENDER_PASSWORD", "")  # Set in .env

def send_real_email(to_email, subject, body, is_html=False):
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        
        subtype = 'html' if is_html else 'plain'
        msg.attach(MIMEText(body, subtype))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        text = msg.as_string()
        server.sendmail(SENDER_EMAIL, to_email, text)
        server.quit()
        print(f"EMAIL SENT to {to_email}")
        return True
    except Exception as e:
        print(f"EMAIL FAILED: {e}")
        return False

# Redis Config
# INTERNAL SERVICE: Runs entirely in memory, no OS-setup required
redis_client = fakeredis.FakeStrictRedis()

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Create table if not exists (Version 1)
    c.execute('''
        CREATE TABLE IF NOT EXISTS pending_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flat_id TEXT NOT NULL,
            visitor_phone TEXT NOT NULL,
            visitor_email TEXT, -- Added in Phase 3.5
            purpose TEXT NOT NULL,
            notes TEXT,
            valid_from DATETIME NOT NULL,
            valid_to DATETIME NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Phase 4: Visitor Faces Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS visitor_faces (
            visitor_phone TEXT PRIMARY KEY,
            face_encoding BLOB, -- Numpy array bytes
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Migration: Add visitor_email if missing (for existing databases)
    try:
        c.execute("ALTER TABLE pending_visits ADD COLUMN visitor_email TEXT")
    except sqlite3.OperationalError:
        pass # Column likely exists
    
    conn.commit()
    conn.close()

init_db()

# --- Auth Helpers ---
def is_society(ip):
    # Troubleshooting Mode: ALLOW ALL
    return True
    
    # Strict Mode (Restored later)
    # if ip in ["127.0.0.1", "localhost", "::1"]: return True
    # if ip.startswith("192.168."): return True
    # return ip.startswith("172.20.10.")

def verify_sig(flat, nonce, sig):
    msg = f"{flat}{nonce}".encode()
    expected = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)

# --- Pydantic Models ---
class VisitorRequest(BaseModel):
    visitor_phone: str
    visitor_email: str = None # Added
    purpose: str
    notes: str = None

# --- Routes ---

@app.middleware("http")
async def check_network(request: Request, call_next):
    # Skip check for webhook since it comes from Exotel
    if request.url.path == "/exotel-webhook":
         return await call_next(request)
         
    # Global check for society network
    client_ip = request.client.host
    if not is_society(client_ip):
         return HTMLResponse("<h2>Access Denied: Not on Society Wi-Fi</h2>", status_code=403)
    response = await call_next(request)
    return response

@app.get("/")
def home(request: Request):
    return HTMLResponse("<h1>Welcome to the Society Portal (Phase 3)</h1>")

@app.get("/flat-entry")
def flat_entry(flat: str, nonce: str, sig: str, request: Request):
    if not verify_sig(flat, nonce, sig):
        return HTMLResponse("Invalid Flat QR", status_code=403)

    # Secure Session
    request.session["flat"] = flat
    
    # Redirect to the registration page
    return RedirectResponse(url="/visitor-registration", status_code=303)

@app.get("/register", response_class=HTMLResponse) # Renamed for simplicity
@app.get("/visitor-registration", response_class=HTMLResponse)
def visitor_registration(request: Request, flat: str = None):
    # Support query param for direct links (e.g. sent via WhatsApp)
    if flat:
        request.session["flat"] = flat
        flat_id = flat
    else:
        flat_id = request.session.get("flat")
    
    if not flat_id:
        # For testing/kiosk, maybe default to "LOBBY"?
        # Or return explicit error asking for flat number
        return templates.TemplateResponse("visitor_registration.html", {"request": request, "flat_id": "LOBBY-GUEST"}) # Defaulting for ease of access
        # return HTMLResponse("<h2>Unauthorized: Please scan your Flat QR code first.</h2>", status_code=403)
        
    return templates.TemplateResponse("visitor_registration.html", {"request": request, "flat_id": flat_id})

@app.post("/register-visitor")
def register_visitor(visitor_data: VisitorRequest, request: Request):
    flat_id = request.session.get("flat")
    if not flat_id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    now = datetime.datetime.now()
    
    # Determine validity based on purpose (Basic logic for now)
    if visitor_data.purpose.lower() == "delivery":
        duration = datetime.timedelta(minutes=30)
    elif visitor_data.purpose.lower() == "service":
        duration = datetime.timedelta(hours=4)
    else: # Guest/Other
        duration = datetime.timedelta(hours=24) # Default 1 day
        
    valid_to = now + duration

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT INTO pending_visits (flat_id, visitor_phone, visitor_email, purpose, notes, valid_from, valid_to)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (flat_id, visitor_data.visitor_phone, visitor_data.visitor_email, visitor_data.purpose, visitor_data.notes, now, valid_to))
    conn.commit()
    conn.close()

    return {"message": "Visitor registered successfully", "valid_until": valid_to}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM pending_visits ORDER BY created_at DESC")
    visits = c.fetchall()
    conn.close()
    return templates.TemplateResponse("dashboard.html", {"request": request, "visits": visits})

# --- Phase 3: Twilio Webhook ---

# --- Phase 3.5: Manual Arrival & Email ---
# Replaces Webhook logic
class ArrivedRequest(BaseModel):
    visit_id: int

@app.post("/mark-arrived")
def mark_arrived(data: ArrivedRequest):
    visit_id = data.visit_id
    
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM pending_visits WHERE id = ?", (visit_id,))
    visit = c.fetchone()
    conn.close()
    
    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")

    if visit["status"] != 'PENDING':
        raise HTTPException(status_code=400, detail="Visit is not pending")

    # 1. Generate Redis Token (Single-use, 60s TTL)
    token = str(uuid.uuid4())
    token_data = {
        "visit_id": visit["id"],
        "flat_id": visit["flat_id"],
        "visitor_phone": visit["visitor_phone"]
    }
    
    # Store in Redis
    redis_client.setex(f"token:{token}", 60, str(token_data))
    
    # 2. Generate QR Code Link
    qr_link = f"http://172.20.10.7:8000/verify-token?token={token}"
    
    # 3. Send Email
    visitor_email = visit["visitor_email"]
    
    # Encode URL for QR API
    import urllib.parse
    encoded_qr_link = urllib.parse.quote(qr_link)
    qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={encoded_qr_link}"
    
    email_body = f"""
    <html>
      <body>
        <h2>Welcome to SocietyGuard!</h2>
        <p>You have been marked as arrived at the gate.</p>
        
        <p><strong>Show this QR Code to the Security Guard:</strong></p>
        <img src="{qr_img_url}" alt="Gate Pass QR" style="width:200px;height:200px;border:1px solid #ccc;"/>
        
        <div style="margin-top: 20px; padding: 10px; background-color: #f3f4f6; border-radius: 5px;">
            <p><strong>Raw Token (for Simulation):</strong></p>
            <code style="font-size: 1.2em; color: #d97706;">{token}</code>
        </div>
        
        <p><i>This QR code is valid for <strong>60 seconds</strong> only.</i></p>
        
        <p>Or click here: <a href="{qr_link}">{qr_link}</a></p>
        
        <p>If this wasn't you, please contact security immediately.</p>
      </body>
    </html>
    """
    
    print(f"Sending email to {visitor_email}...")
    # Send Real Email (HTML)
    # Update send_real_email to handle HTML (it uses MIMEMultipart/MIMEText(..., 'plain') currently)
    # I need to change 'plain' to 'html' in the helper function too, or make it dynamic.
    # I will update the helper function in a separate edit or assume I can hack it here? 
    # Better to update helper first or inline.
    # I will just update the helper call to pass 'html' if I modify the helper. 
    # WAIT: verify send_real_email implementation. 
    # It has `msg.attach(MIMEText(body, 'plain'))`.
    # I need to change that line.
    
    email_success = send_real_email(visitor_email, "SocietyGuard Gate Pass (60s Validity)", email_body, is_html=True)
    
    return {"message": "Marked Arrived. QR Email Sent.", "qr_link": qr_link, "email_success": email_success}


# Phase 4: Face Recognition (DeepFace)
try:
    from deepface import DeepFace
    FACE_REC_AVAILABLE = True
except ImportError:
    FACE_REC_AVAILABLE = False
    print("WARNING: deepface not installed. Anomaly detection disabled.")

@app.get("/guard")
def guard_scanner(request: Request):
    return templates.TemplateResponse("guard_scanner.html", {"request": request})

@app.post("/verify-entry")
async def verify_entry(token: str = Form(...), image_file: UploadFile = File(...)):
    # 1. Verify Token from Redis
    raw_data = redis_client.get(f"token:{token}")
    if not raw_data:
         return JSONResponse({"status": "invalid", "detail": "Token Expired or Invalid"}, status_code=400)
    
    # Parse Token Data
    import ast
    try:
        token_data = ast.literal_eval(raw_data.decode('utf-8'))
    except:
        token_data = eval(raw_data)

    visitor_phone = token_data["visitor_phone"]
    
    # 2. Process Image
    contents = await image_file.read()
    
    if not FACE_REC_AVAILABLE:
        redis_client.delete(f"token:{token}") 
        return {"status": "approved", "detail": "Token Valid (Face Check Skipped - Missing Lib)"}

    # Save incoming image temporarily
    temp_filename = f"temp_{token}.jpg"
    with open(temp_filename, "wb") as f:
        f.write(contents)

    status = "approved"
    detail = "Access Granted"

    # 3. Check DB for previous face
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT face_encoding FROM visitor_faces WHERE visitor_phone = ?", (visitor_phone,))
    row = c.fetchone()
    
    if row:
        # We have a Previous Image (stored as bytes blob of the JPG)
        ref_filename = f"ref_{visitor_phone}.jpg"
        with open(ref_filename, "wb") as f:
            f.write(row[0])
            
        try:
            # DeepFace Verify
            # model_name='VGG-Face' is good, 'ArcFace' better. Default is VGG-Face.
            result = DeepFace.verify(img1_path=temp_filename, img2_path=ref_filename, enforce_detection=False)
            
            if result['verified']:
                detail = "Identity Verified (DeepFace Match)"
                c.execute("UPDATE visitor_faces SET last_seen = CURRENT_TIMESTAMP WHERE visitor_phone = ?", (visitor_phone,))
            else:
                status = "anomaly"
                detail = f"ANOMALY: Face Mismatch! (Distance: {result['distance']})"
                
            # Cleanup Ref
            if os.path.exists(ref_filename):
                os.remove(ref_filename)
                
        except Exception as e:
            print(f"DeepFace Error: {e}")
            detail = f"Face Check Error: {e}"
            
    else:
        # New Registration
        detail = "Access Granted (New Face Registered)"
        # Store the RAW BYTES of the image (contents) as the 'encoding' for DeepFace (it uses reference images)
        # Re-using the Blob column to store JPG bytes instead of numpy encoding
        c.execute("INSERT INTO visitor_faces (visitor_phone, face_encoding) VALUES (?, ?)", (visitor_phone, contents))
    
    conn.commit()
    conn.close()
    
    # Cleanup Temp
    if os.path.exists(temp_filename):
        os.remove(temp_filename)
        
    # Approve Entry even if Anomaly? User said "it accepts the user in" but wanted alerting.
    # If status is anomaly, we do NOT delete token? Or we do?
    # Usually we consume token.    
    if status == "approved":
        # Log Token Usage for Phase 5 Correlation
        # Format: TIMESTAMP|TOKEN_ID|TYPE
        log_entry = f"{time.time()}|{token}|FACE_ENTRY"
        redis_client.lpush("gate_access_logs", log_entry)
        redis_client.ltrim("gate_access_logs", 0, 999) # Keep last 1000 logs
        
        redis_client.delete(f"token:{token}") # Burn token
        
    return {"status": status, "detail": detail}


@app.get("/verify-token")
def verify_token(token: str):
    # Check Redis
    raw_data = redis_client.get(f"token:{token}")
    if not raw_data:
        return JSONResponse({"status": "invalid", "detail": "Token Expired or Invalid"}, status_code=400)
    
    # Valid
    redis_client.delete(f"token:{token}") # Burn token (Single Use)
    
    # Log Usage
    log_entry = f"{time.time()}|{token}|QR_ENTRY"
    redis_client.lpush("gate_access_logs", log_entry)
    
    return {"status": "valid", "detail": "Access Granted (One-Time Token Consumed)"}

# --- Phase 5: Gate AI Endpoint ---
@app.get("/gate-verification", response_class=HTMLResponse)
def gate_verification_page(request: Request):
    return templates.TemplateResponse("gate_verification.html", {"request": request})

@app.post("/verify-video")
async def verify_video(gate_x: int = Form(...), inner_x: int = Form(...), video: UploadFile = File(...)):
    if not gate_ai_engine:
        return {"error": "AI Engine not available"}
        
    # Save Video Temp
    temp_name = f"temp_vid_{uuid.uuid4()}.mp4"
    with open(temp_name, "wb") as f:
        f.write(await video.read())
        
    # Run Pipeline
    pipeline_output = gate_ai_engine.process_video(temp_name, gate_x, inner_x, redis_client)
    
    # Cleanup
    if os.path.exists(temp_name):
        os.remove(temp_name)
        
    return pipeline_output
