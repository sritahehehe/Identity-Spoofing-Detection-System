import hmac, hashlib
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecretkey")

SECRET = b"supersecretkey"

def is_society(ip):
    # Allow localhost for testing + Society Subnet
    if ip in ["127.0.0.1", "localhost", "::1"]:
        return True
    return ip.startswith("172.20.10.")

def verify(flat, nonce, sig):
    msg = f"{flat}{nonce}".encode()
    expected = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)

@app.get("/")
def home(request: Request):
    ip = request.client.host
    if not is_society(ip):
        return HTMLResponse("<h2>Access Denied: Not on Society Wi-Fi</h2>", status_code=403)
    return HTMLResponse("<h1>Welcome to the Society Portal</h1>")

@app.get("/flat-entry")
def flat_entry(flat: str, nonce: str, sig: str, request: Request):
    ip = request.client.host
    if not ip.startswith("192.168.29."):
        return HTMLResponse("Not on society Wi-Fi", status_code=403)

    if not verify(flat, nonce, sig):
        return HTMLResponse("Invalid Flat QR", status_code=403)

    # create session
    request.session["flat"] = flat

    return HTMLResponse(f"""
        <h2>Flat {flat} Verified</h2>
        <a href="/register">Register Visitor</a>
    """)
