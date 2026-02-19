import sqlite3

def reset_users():
    db_path = "aqi.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check current count
        cursor.execute("SELECT COUNT(*) FROM users")
        count = cursor.fetchone()[0]
        print(f"Current user count: {count}")
        
        confirm = input("Are you sure you want to DELETE ALL users? (yes/no): ")
        if confirm.lower() == "yes":
            cursor.execute("DELETE FROM users")
            conn.commit()
            print("✅ All users have been deleted.")
        else:
            print("❌ Operation cancelled.")
            
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    reset_users()