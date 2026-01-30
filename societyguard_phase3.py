import hmac
import hashlib
import sqlite3
import datetime
import uuid
import qrcode
import os
import fakeredis  # CHANGED: Internal Embedded Redis Service
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from twilio.request_validator import RequestValidator
from twilio.rest import Client

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecretkey")

# Setup Templates and Static
templates = Jinja2Templates(directory="templates")
os.makedirs("static/qrcodes", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

SECRET = b"supersecretkey"
DB_NAME = "iam_society.db"

# Twilio Config
TWILIO_ACCOUNT_SID = "REDACTED_TWILIO_SID"
TWILIO_AUTH_TOKEN = "38e613de7f4f20dc155d1d01484d69b9"
TWILIO_PHONE_NUMBER = "+13184947924" # Updated with user provided number

# Redis Config
# INTERNAL SERVICE: Runs entirely in memory, no OS-setup required
redis_client = fakeredis.FakeStrictRedis()

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS pending_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flat_id TEXT NOT NULL,
            visitor_phone TEXT NOT NULL,
            purpose TEXT NOT NULL,
            notes TEXT,
            valid_from DATETIME NOT NULL,
            valid_to DATETIME NOT NULL,
            status TEXT DEFAULT 'PENDING',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- Auth Helpers ---
def is_society(ip):
    # In production, this should match your strict subnet
    return ip.startswith("172.20.10.")

def verify_sig(flat, nonce, sig):
    msg = f"{flat}{nonce}".encode()
    expected = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)

# --- Pydantic Models ---
class VisitorRequest(BaseModel):
    visitor_phone: str
    purpose: str
    notes: str = None

# --- Routes ---

@app.middleware("http")
async def check_network(request: Request, call_next):
    # Skip check for webhook since it comes from Twilio (or ngrok)
    if request.url.path == "/twilio-webhook":
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

@app.get("/visitor-registration", response_class=HTMLResponse)
def visitor_registration(request: Request):
    flat_id = request.session.get("flat")
    if not flat_id:
        return HTMLResponse("<h2>Unauthorized: Please scan your Flat QR code first.</h2>", status_code=403)
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
        INSERT INTO pending_visits (flat_id, visitor_phone, purpose, notes, valid_from, valid_to)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (flat_id, visitor_data.visitor_phone, visitor_data.purpose, visitor_data.notes, now, valid_to))
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

@app.post("/twilio-webhook")
async def twilio_webhook(request: Request):
    # 1. Verify Request Signature
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    form_data = await request.form()
    # url = str(request.url) # Note: If behind ngrok, this needs to be the ngrok URL
    # signature = request.headers.get("X-Twilio-Signature", "")
    
    # Skipping strict signature validation for local/ngrok dev unless fully configured env
    # if not validator.validate(url, dict(form_data), signature):
    #    raise HTTPException(status_code=403, detail="Invalid Twilio Signature")

    caller_phone = form_data.get("From")
    
    if not caller_phone:
        return Response(content="<Response></Response>", media_type="application/xml")

    print(f"Incoming call from: {caller_phone}")

    # 2. Check Database for Valid PENDING Visit
    now = datetime.datetime.now()
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Normalize phone: removing '+' or spaces might be needed depending on input format
    # For now assuming exact match or user input matches Twilio format (E.164)
    c.execute('''
        SELECT * FROM pending_visits 
        WHERE visitor_phone = ? 
        AND status = 'PENDING'
        AND ? BETWEEN valid_from AND valid_to
        ORDER BY created_at DESC
        LIMIT 1
    ''', (caller_phone, now))
    
    visit = c.fetchone()
    conn.close()

    if not visit:
        print("No valid visit found for this number.")
        return Response(content="<Response><Reject/></Response>", media_type="application/xml")

    # 3. Generate Redis Token (Single-use, 60s TTL)
    token = str(uuid.uuid4())
    token_data = {
        "visit_id": visit["id"],
        "flat_id": visit["flat_id"],
        "visitor_phone": caller_phone
    }
    
    # Store in Redis
    redis_client.setex(f"token:{token}", 60, str(token_data))
    
    print(f"Generated Token: {token} (TTL 60s)")

    # 4. Generate QR Code
    # The URL the visitor will access (must be accessible to them)
    # Using local IP for now as they are "at the gate" (Society Wi-Fi)
    qr_link = f"http://172.20.10.7:8000/verify-token?token={token}"
    
    img = qrcode.make(qr_link)
    qr_filename = f"static/qrcodes/{token}.png"
    img.save(qr_filename)

    # 5. Send SMS with QR Link
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    
    # Use the From number as To
    try:
        # In production this would be an MMS with media_url, or a link to the image
        # Since local image isn't public, we send a link they can open on Wi-Fi
        sms_body = f"Welcome to SocietyGuard. Your Gate Pass: {qr_link}"
        
        print(f"Attempting SMS FROM: {form_data.get('To')} TO: {caller_phone}")
        message = client.messages.create(
            body=sms_body,
            from_=form_data.get("To"),
            to=caller_phone
        )
        print(f"SMS SENT via Twilio to {caller_phone}: {message.sid}")
    except Exception as e:
        error_msg = f"SMS ERROR: {repr(e)}"
        print(error_msg)
        with open("sms_error.log", "w") as f:
            f.write(error_msg)

    return Response(content="<Response><Say>Pass generated. Check your SMS.</Say></Response>", media_type="application/xml")


@app.get("/verify-token")
def verify_token(token: str):
    # Check Redis
    data = redis_client.get(f"token:{token}")
    
    if not data:
        return HTMLResponse("<h1>Invalid or Expired Token</h1>", status_code=403)

    # Delete token (Single-use)
    redis_client.delete(f"token:{token}")
    
    return HTMLResponse(f"<h1>Access Granted</h1><p>Token Verified. Welcome!</p><p>Debug Data: {data}</p>")
