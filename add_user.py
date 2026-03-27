import bcrypt
import sqlite3
import sys

def add_user(full_name, email, password, role='qa_lead'):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    conn = sqlite3.connect('db/qa_testing.db')
    try:
        conn.execute(
            "INSERT INTO users (full_name, email, password_hash, role) VALUES (?,?,?,?)",
            (full_name, email, hashed, role)
        )
        conn.commit()
        print(f"✅ User '{email}' added with role '{role}'.")
    except sqlite3.IntegrityError:
        print(f"❌ Email '{email}' already exists.")
    finally:
        conn.close()

# Run from terminal:
# python3 add_user.py "John Doe" "john@ishir.com" "Password@123" "qa_lead"
if __name__ == "__main__":
    if len(sys.argv) == 5:
        add_user(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print("Usage: python3 add_user.py <full_name> <email> <password> <role>")
        print("Roles: admin | qa_lead | developer")