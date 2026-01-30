import hmac
import hashlib
import sqlite3
import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecretkey")

# Setup Templates
templates = Jinja2Templates(directory="templates")

SECRET = b"supersecretkey"
DB_NAME = "iam_society.db"

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
    # Global check for society network
    client_ip = request.client.host
    if not is_society(client_ip):
         return HTMLResponse("<h2>Access Denied: Not on Society Wi-Fi</h2>", status_code=403)
    response = await call_next(request)
    return response

@app.get("/")
def home(request: Request):
    return HTMLResponse("<h1>Welcome to the Society Portal (Phase 2)</h1>")

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
