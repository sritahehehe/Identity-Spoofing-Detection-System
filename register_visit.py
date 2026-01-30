import sqlite3
import datetime

DB_NAME = "iam_society.db"
VISITOR_PHONE = "+918328872957" # Updated to India (+91)
FLAT_ID = "101"

conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

now = datetime.datetime.now()
valid_to = now + datetime.timedelta(hours=1)

print(f"Registering visit for {VISITOR_PHONE} (Flat {FLAT_ID})...")
c.execute('''
    INSERT INTO pending_visits (flat_id, visitor_phone, purpose, notes, valid_from, valid_to, status)
    VALUES (?, ?, ?, ?, ?, ?, 'PENDING')
''', (FLAT_ID, VISITOR_PHONE, "Test Visit", "Auto-generated", now, valid_to))

conn.commit()
visit_id = c.lastrowid
print(f"Visit registered! ID: {visit_id}")
conn.close()
