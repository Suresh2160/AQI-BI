import sqlite3
import pandas as pd

def view_users():
    try:
        conn = sqlite3.connect("aqi.db")
        print("--- Users in aqi.db ---")
        # We select all columns to see username and hashed password
        df = pd.read_sql_query("SELECT * FROM users", conn)
        print(df)
        conn.close()
    except Exception as e:
        print(f"Error reading database: {e}")

if __name__ == "__main__":
    view_users()