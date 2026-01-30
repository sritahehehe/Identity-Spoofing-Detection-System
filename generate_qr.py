import qrcode, hmac, hashlib, secrets

flat = "101"
nonce = secrets.token_hex(4)
msg = f"{flat}{nonce}".encode()
sig = hmac.new(b"supersecretkey", msg, hashlib.sha256).hexdigest()

url = f"http://172.20.10.7:8000/flat-entry?flat={flat}&nonce={nonce}&sig={sig}"

img = qrcode.make(url)
img.save("flat101_qr.png")

print("QR URL:", url)
print("QR code saved as flat101_qr.png")
with open("latest_qr_url.txt", "w") as f:
    f.write(url)
