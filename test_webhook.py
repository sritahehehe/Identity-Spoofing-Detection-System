import requests
import datetime

# Configuration
SERVER_URL = "http://127.0.0.1:8000/twilio-webhook"
VISITOR_PHONE = "+918328872957" # Updated to India (+91)
SOCIETY_PHONE = "+13184947924"

# Simulate Twilio Webhook Payload
payload = {
    "From": VISITOR_PHONE,
    "To": SOCIETY_PHONE,
    "CallSid": "CA1234567890abcdef",
    "Body": "Hello" # Or whatever Twilio sends
}

# Send Request
print(f"Simulating Call from {VISITOR_PHONE}...")
try:
    response = requests.post(SERVER_URL, data=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response Body: {response.text}")
except Exception as e:
    print(f"Error: {e}")
