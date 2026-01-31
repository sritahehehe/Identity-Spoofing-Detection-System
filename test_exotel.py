import requests
import time

# Configuration
SERVER_URL = "http://127.0.0.1:8000/exotel-webhook"

# Exotel Payload Format (Standard Passthrough)
# They send CallSid, CallFrom, CallTo, etc.
# visitor_phone matches what we registered: +918328872957
VISITOR_PHONE = "+918328872957" 
EXOTEL_PHONE = "08088888888" # The number stored in env/config

payload = {
    "CallSid": "dae560a0-test-call-sid",
    "CallFrom": VISITOR_PHONE,
    "To": EXOTEL_PHONE,
    "Direction": "inbound",
    "Created": "2023-10-27 10:00:00"
}

print(f"Simulating Exotel Call from {VISITOR_PHONE}...")
print(f"Target: {SERVER_URL}")

try:
    response = requests.post(SERVER_URL, data=payload) # Form-encoded
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text}")
except Exception as e:
    print(f"Error: {e}")
