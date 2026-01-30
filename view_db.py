import sqlite3

conn = sqlite3.connect("iam_society.db")
c = conn.cursor()

print("--- Pending Visits ---")
c.execute("SELECT * FROM pending_visits")
rows = c.fetchall()

if not rows:
    print("No visits found.")
else:
    for row in rows:
        print(f"ID: {row[0]}")
        print(f"Flat: {row[1]}")
        print(f"Visitor: {row[2]}")
        print(f"Purpose: {row[3]}")
        print(f"Notes: {row[4]}")
        print(f"Valid From: {row[5]}")
        print(f"Valid To: {row[6]}")
        print("-" * 20)

conn.close()
