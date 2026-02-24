import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
import pandas as pd
import sqlite3
import plotly.express as px
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import IsolationForest
from fpdf import FPDF
import numpy as np
import requests
import bcrypt
from openai import OpenAI
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import smtplib
from email.message import EmailMessage
import io
import schedule
import time
import threading
import folium
from streamlit.components.v1 import html as st_html
from streamlit_mic_recorder import speech_to_text
from folium.plugins import HeatMap, TimestampedGeoJson
import extra_streamlit_components as stx
import html


def get_secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except StreamlitSecretNotFoundError:
        return default


def fetch_aqi_news(api_key):

    url = (
        f"https://newsapi.org/v2/everything?"
        f"q=air%20pollution%20OR%20air%20quality"
        f"&language=en"
        f"&sortBy=publishedAt"
        f"&pageSize=20"
        f"&apiKey={api_key}"
    )

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get("status") != "ok":
            return []

        return data.get("articles", [])

    except Exception as e:
        return []


# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="AQI Dashboard", page_icon=None, layout="wide")

# ---------------- SESSION INIT ----------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "user" not in st.session_state:
    st.session_state.user = ""

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "chat_open" not in st.session_state:
    st.session_state.chat_open = False

if "role" not in st.session_state:
    st.session_state.role = "user"

# ---------------- COOKIE MANAGER ----------------
cookie_manager = stx.CookieManager()
cookie_theme = cookie_manager.get(cookie="theme")

if "theme" not in st.session_state:
    # If cookie exists, use it; otherwise default to Dark
    st.session_state.theme = cookie_theme if cookie_theme else "Light"

# Load Layout Preference
cookie_layout = cookie_manager.get(cookie="layout")
if "layout_pref" not in st.session_state:
    default_layout = [
        "Overview",
        "Maps",
        "Trends & Charts",
        "Pollutant Analysis",
        "Deep Dive",
        "Advanced Analytics",
    ]
    st.session_state.layout_pref = (
        cookie_layout.split(",") if cookie_layout else default_layout
    )


# ---------------- OPENAI CLIENT ----------------
openai_api_key = get_secret("OPENAI_API_KEY")
client = OpenAI(api_key=openai_api_key) if openai_api_key else None

if st.button("Test OpenAI"):
    if not client:
        st.warning("OpenAI API key missing. Add `OPENAI_API_KEY` to Streamlit secrets.")
    else:
        try:
            test = client.chat.completions.create(
                model="gpt-4o-mini", messages=[{"role": "user", "content": "Hello"}]
            )
            st.write(test.choices[0].message.content)
        except Exception as e:
            st.error(f"OpenAI request failed: {e}")


# ---------------- LOCATION DETECTION ----------------
def get_user_location():
    try:
        res = requests.get("https://ipapi.co/json/", timeout=5)
        data = res.json()
        city = data.get("city", "Unknown")
        region = data.get("region", "Unknown")
        country = data.get("country_name", "Unknown")
        return city, region, country
    except:
        return "Unknown", "Unknown", "Unknown"


# ---------------- WEATHER API ----------------
@st.cache_data(ttl=1800)
def get_weather_data(city):
    try:
        url = f"https://wttr.in/{city}?format=j1"
        response = requests.get(url, timeout=5)

        if response.status_code != 200:
            return None

        data = response.json()
        current = data.get("current_condition", [{}])[0]

        return {
            "temp": current.get("temp_C"),
            "humidity": current.get("humidity"),
            "desc": current.get("weatherDesc", [{}])[0].get("value"),
            "wind": current.get("windspeedKmph"),
            "wind_dir": current.get("winddirDegree"),
        }
    except Exception:
        return None


# ---------------- CITY COORDINATES ----------------
CITY_COORDINATES = {
    "Ahmedabad": [23.0225, 72.5714],
    "Aizawl": [23.7271, 92.7176],
    "Amaravati": [16.5417, 80.5158],
    "Amritsar": [31.6340, 74.8723],
    "Bengaluru": [12.9716, 77.5946],
    "Bhopal": [23.2599, 77.4126],
    "Brajrajnagar": [21.8333, 83.9167],
    "Chandigarh": [30.7333, 76.7794],
    "Chennai": [13.0827, 80.2707],
    "Coimbatore": [11.0168, 76.9558],
    "Delhi": [28.6139, 77.2090],
    "Ernakulam": [9.9816, 76.2999],
    "Gurugram": [28.4595, 77.0266],
    "Guwahati": [26.1445, 91.7362],
    "Hyderabad": [17.3850, 78.4867],
    "Jaipur": [26.9124, 75.7873],
    "Jorapokhar": [23.7000, 86.4100],
    "Kochi": [9.9312, 76.2673],
    "Kolkata": [22.5726, 88.3639],
    "Lucknow": [26.8467, 80.9462],
    "Mumbai": [19.0760, 72.8777],
    "Patna": [25.5941, 85.1376],
    "Shillong": [25.5788, 91.8933],
    "Talcher": [20.9500, 85.2167],
    "Thiruvananthapuram": [8.5241, 76.9366],
    "Visakhapatnam": [17.6868, 83.2185],
}


def add_coordinates(df):
    df["Lat"] = df["City"].map({k: v[0] for k, v in CITY_COORDINATES.items()})
    df["Lon"] = df["City"].map({k: v[1] for k, v in CITY_COORDINATES.items()})
    return df


# ---------------- NEWS API ----------------


# ---------------- SCHEDULER & EMAIL ----------------
def send_daily_report_email():
    # Fetch subscribed users from DB (Fixed: st.session_state is not available in background threads)
    try:
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute("SELECT username FROM users WHERE subscription=1")
        users = cursor.fetchall()
        conn.close()

        for user in users:
            user_email = user[0]
            if "@" in user_email:
                subject = (
                    f"Daily AQI Report - {pd.Timestamp.now().strftime('%Y-%m-%d')}"
                )
                print(f"Simulating email to {user_email}: {subject}")
                # send_reset_email(user_email) # Uncomment to actually send if SMTP is configured
    except Exception as e:
        print(f"Scheduler Error: {e}")


def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)


# Start Scheduler in Background Thread (Singleton pattern for Streamlit)
if "scheduler_active" not in st.session_state:
    # Schedule the job
    schedule.every().day.at("09:00").do(send_daily_report_email)

    # Start thread
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()
    st.session_state.scheduler_active = True


# ---------------- MAP UTILS ----------------
def get_wind_arrow_icon(angle, speed):
    # Create a rotated arrow div
    try:
        angle = int(angle) if angle is not None else 0
    except Exception:
        angle = 0
    try:
        speed_int = int(speed) if speed is not None else 0
    except Exception:
        speed_int = 0

    return folium.DivIcon(
        html=f"""
        <div style="transform: rotate({angle}deg); font-size: 24px; color: {'#ff4b4b' if speed_int > 20 else '#00c9ff'}; text-shadow: 0 0 5px black;">
            ➤
        </div>
    """
    )


# ---------------- USER DATABASE (Signup/Login) ----------------
def init_user_db():
    conn = sqlite3.connect("aqi.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'user'
        )
    """
    )

    # Migration: Add role column if it doesn't exist (for existing databases)
    try:
        cursor.execute("SELECT role FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")

    # Create Default Admin if not exists
    cursor.execute("SELECT * FROM users WHERE role='admin'")
    if not cursor.fetchone():
        admin_pw = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
        try:
            cursor.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                ("admin", admin_pw, "admin"),
            )
        except sqlite3.IntegrityError:
            pass

    # Migration: Add profile_pic column if it doesn't exist
    try:
        cursor.execute("SELECT profile_pic FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN profile_pic BLOB")

    # Create Activity Logs Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Create Feedback Table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            city TEXT,
            issue_type TEXT,
            description TEXT,
            status TEXT DEFAULT 'Pending',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Migration: Add status column to feedback if it doesn't exist
    try:
        cursor.execute("SELECT status FROM feedback LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE feedback ADD COLUMN status TEXT DEFAULT 'Pending'")

    # Migration: Add subscription column to users
    try:
        cursor.execute("SELECT subscription FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN subscription INTEGER DEFAULT 0")

    # Migration: Add favorite_cities column to users
    try:
        cursor.execute("SELECT favorite_cities FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN favorite_cities TEXT")

    # Create Settings Table (For Maintenance Mode)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """
    )
    cursor.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('maintenance_mode', 'false')"
    )

    conn.commit()
    conn.close()


def log_user_activity(username, action):
    try:
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO activity_logs (username, action) VALUES (?, ?)",
            (username, action),
        )
        conn.commit()
        conn.close()
    except:
        pass


# ---------------- SETTINGS HELPERS ----------------
def get_maintenance_mode():
    conn = sqlite3.connect("aqi.db")
    c = conn.cursor()
    try:
        c.execute("SELECT value FROM settings WHERE key='maintenance_mode'")
        row = c.fetchone()
        return row[0] == "true" if row else False
    except:
        return False
    finally:
        conn.close()


def set_maintenance_mode(status):
    conn = sqlite3.connect("aqi.db")
    c = conn.cursor()
    val = "true" if status else "false"
    c.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('maintenance_mode', ?)",
        (val,),
    )
    conn.commit()
    conn.close()


def get_activity_log_for_feedback(feedback_id):
    try:
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT activity_logs.* FROM activity_logs
            JOIN feedback ON activity_logs.username = feedback.username
            WHERE feedback.id = ?
        """,
            (feedback_id,),
        )
        log = cursor.fetchone()
        conn.close()
        return log
    except:
        return None


def signup_user(username, password):
    conn = sqlite3.connect("aqi.db")
    cursor = conn.cursor()

    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        cursor.execute(
            "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
            (username, hashed_pw, "user"),
        )
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def login_user(username, password):
    conn = sqlite3.connect("aqi.db")
    cursor = conn.cursor()

    cursor.execute(
        "SELECT password, role, favorite_cities FROM users WHERE username=?",
        (username,),
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        stored_hash = row[0].encode()
        if bcrypt.checkpw(password.encode(), stored_hash):
            # Parse favorites
            favs = row[2].split(",") if row[2] else []
            return True, row[1], favs  # Return Success, Role, Favorites
    return False, None, []


def login_google_user(token):
    try:
        # Replace with your actual Google Client ID
        CLIENT_ID = "YOUR_GOOGLE_CLIENT_ID"
        id_info = id_token.verify_oauth2_token(
            token, google_requests.Request(), CLIENT_ID
        )
        email = id_info["email"]

        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT username, role, favorite_cities FROM users WHERE username=?",
            (email,),
        )
        row = cursor.fetchone()

        if not row:
            # Register new Google user with a placeholder password
            cursor.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (email, "GOOGLE_AUTH", "user"),
            )
            conn.commit()
            role = "user"
            favs = []
        else:
            role = row[1]
            favs = row[2].split(",") if row[2] else []
        conn.close()
        return email, role, favs
    except ValueError:
        return None, None, []


def send_reset_email(to_email):
    # ---------------- CONFIG ----------------
    # Replace these with your actual email credentials
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    SENDER_EMAIL = "your_email@gmail.com"
    SENDER_PASSWORD = "your_app_password"
    # ----------------------------------------

    msg = EmailMessage()
    msg.set_content(
        f"Hello,\n\nWe received a request to reset your password.\nClick the link below to proceed:\nhttp://localhost:8501/?reset_token=dummy_token_for_{to_email}\n\nIf you did not request this, please ignore this email."
    )
    msg["Subject"] = "AQI Dashboard - Password Reset Request"
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email

    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Email Error: {e}")
        return False


init_user_db()


# ---------------- AQI DATABASE (SQLite) ----------------
@st.cache_data(ttl=600)  # Cache data for 10 minutes to optimize performance
def get_data():
    conn = sqlite3.connect("aqi.db")
    query = "SELECT City, Date, AQI, PM25, PM10, NO2, SO2, CO, O3 FROM air_quality"
    df = pd.read_sql_query(query, conn)
    conn.close()

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.replace({np.nan: None})
    return df


df = get_data()


# ---------------- AQI CATEGORY ----------------
def aqi_category(aqi):
    if aqi is None:
        return "Unknown"
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Satisfactory"
    elif aqi <= 200:
        return "Moderate"
    elif aqi <= 300:
        return "Poor"
    elif aqi <= 400:
        return "Very Poor"
    else:
        return "Severe"


df["AQI_Category"] = df["AQI"].apply(aqi_category)


# ---------------- SIDEBAR FILTERS FUNCTION ----------------
def render_sidebar_filters(df):
    st.sidebar.markdown("### Filters")

    city_list = sorted(df["City"].dropna().unique())

    # 📍 Manual location selection
    st.sidebar.markdown("#### Select Location")
    manual_city = st.sidebar.selectbox("Choose your City", city_list, key="manual_city")

    suggested_city = manual_city
    location_text = manual_city

    # Multi-city selection
    # Initialize selected_cities with favorites if available and not already set
    if (
        "selected_cities" not in st.session_state
        and "favorite_cities" in st.session_state
    ):
        valid_favs = [c for c in st.session_state.favorite_cities if c in city_list]
        if valid_favs:
            st.session_state.selected_cities = valid_favs

    selected_cities = st.sidebar.multiselect(
        "Select Cities", city_list, default=city_list[:3], key="selected_cities"
    )

    if st.sidebar.button("⭐ Save as Favorites"):
        if st.session_state.get("logged_in"):
            conn = sqlite3.connect("aqi.db")
            c = conn.cursor()
            fav_str = ",".join(selected_cities)
            c.execute(
                "UPDATE users SET favorite_cities=? WHERE username=?",
                (fav_str, st.session_state.user),
            )
            conn.commit()
            conn.close()
            st.session_state.favorite_cities = selected_cities
            st.toast("Favorites saved successfully!")
        else:
            st.error("Please login to save favorites.")

    # Date filter
    min_date = df["Date"].min()
    max_date = df["Date"].max()

    date_range = st.sidebar.date_input(
        "Select Date Range", [min_date, max_date], key="date_range"
    )

    # Alert Threshold
    st.sidebar.markdown("### Alerts")
    alert_threshold = st.sidebar.slider("AQI Alert Threshold", 50, 500, 200, 10)

    # Apply filters
    filtered_df = df[df["City"].isin(selected_cities)]

    if len(date_range) == 2:
        start_date, end_date = date_range
        filtered_df = filtered_df[
            (filtered_df["Date"] >= pd.to_datetime(start_date))
            & (filtered_df["Date"] <= pd.to_datetime(end_date))
        ]

    # Check for Alerts
    if not filtered_df.empty:
        max_aqi_view = filtered_df["AQI"].max()
        if max_aqi_view > alert_threshold:
            st.toast(f"High AQI Alert: {max_aqi_view} detected!")
            st.sidebar.error(f"Alert: AQI > {alert_threshold} detected!")

    return (
        filtered_df,
        selected_cities,
        date_range,
        alert_threshold,
        city_list,
        suggested_city,
        location_text,
    )


# ---------------- AUTH UI ----------------
if st.session_state.logged_in == False:

    # Check for Google Login Token in URL (passed from login.html)
    if "token" in st.query_params:
        token = st.query_params["token"]
        email, role, favs = login_google_user(token)
        if email:
            st.session_state.logged_in = True
            st.session_state.user = email
            st.session_state.role = role
            st.session_state.favorite_cities = favs
            log_user_activity(email, "Login via Google")
            st.success(f"Login Successful with Google: {email}")
            st.rerun()

    # Check for Password Reset Token
    if "reset_token" in st.query_params:
        token = st.query_params["reset_token"]
        try:
            # Simple token parsing (In production, use signed tokens)
            # Token format expected: "dummy_token_for_user@example.com"
            reset_email_user = token.split("_for_")[1]

            st.markdown(
                f"<h3 style='text-align:center;'>Reset Password for {html.escape(reset_email_user)}</h3>",
                unsafe_allow_html=True,
            )

            with st.form("reset_password_form"):
                new_pw_reset = st.text_input("New Password", type="password")
                confirm_pw_reset = st.text_input("Confirm Password", type="password")
                submit_reset = st.form_submit_button("Update Password")

                if submit_reset:
                    if new_pw_reset == confirm_pw_reset:
                        conn = sqlite3.connect("aqi.db")
                        cursor = conn.cursor()
                        hashed_pw = bcrypt.hashpw(
                            new_pw_reset.encode(), bcrypt.gensalt()
                        ).decode()
                        cursor.execute(
                            "UPDATE users SET password=? WHERE username=?",
                            (hashed_pw, reset_email_user),
                        )
                        conn.commit()
                        conn.close()
                        st.success("Password updated successfully! Please login.")
                        st.query_params.clear()
                    else:
                        st.error("Passwords do not match.")
            st.stop()
        except IndexError:
            st.error("Invalid Password Reset Token.")

    col_spacer1, col_login, col_spacer2 = st.columns([1, 2, 1])

    with col_login:
        st.markdown(
            f"""
            <style>
            @keyframes pulse-logo {{
                0% {{ transform: scale(1); }}
                50% {{ transform: scale(1.1); }}
                100% {{ transform: scale(1); }}
            }}
            .logo-text {{
                text-align: center; 
                font-size: 3rem; 
                margin-bottom: 10px;
                animation: pulse-logo 2s infinite;
                background: linear-gradient(90deg, #4facfe, #00f2fe);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                font-weight: bold;
            }}
            </style>
            <div class='login-container'>
                <div class='logo-text'>AQI</div>
                <h1 class='gradient-text' style='text-align:center;'>AQI Dashboard</h1>
                <p style='text-align:center; color: #ccc; margin-bottom: 30px;'>Monitor Air Quality with Real-Time Insights</p>
            </div>
        """,
            unsafe_allow_html=True,
        )

        tab1, tab2 = st.tabs(["Login", "Signup"])

        with tab1:
            st.markdown("### Welcome Back!")
            username = st.text_input(
                "Username", key="login_user", placeholder="Enter your username"
            )
            password = st.text_input(
                "Password",
                type="password",
                key="login_pass",
                placeholder="Enter your password",
            )

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Login", use_container_width=True):
                success, role, favs = login_user(username, password)
                if success:
                    st.session_state.logged_in = True
                    st.session_state.user = username
                    st.session_state.role = role
                    st.session_state.favorite_cities = favs
                    st.session_state.selected_cities = favs  # Auto-load favorites
                    log_user_activity(username, "Login via Password")
                    st.success("Login Successful")
                    st.rerun()
                else:
                    st.error("Invalid username or password")

            with st.expander("Forgot Password?"):
                st.write("Enter your email to receive a reset link.")
                reset_email_input = st.text_input(
                    "Email Address", key="reset_email_input"
                )
                if st.button("Send Reset Link"):
                    if send_reset_email(reset_email_input):
                        st.success(f"Reset link sent to {reset_email_input}")
                    else:
                        st.error("Failed to send email.")

        with tab2:
            st.markdown("### Join Us Today")
            new_username = st.text_input(
                "New Username", key="signup_user", placeholder="Choose a username"
            )
            new_password = st.text_input(
                "New Password",
                type="password",
                key="signup_pass",
                placeholder="Choose a strong password",
            )

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Create Account", use_container_width=True):
                if signup_user(new_username, new_password):
                    log_user_activity(new_username, "New User Signup")
                    st.success("Account Created Successfully. Please Login Now")
                else:
                    st.error("Username already exists")

    st.stop()


# ---------------- SIDEBAR MENU ----------------
st.sidebar.markdown(
    "<h1 class='gradient-text' style='font-size: 1.75rem;'>AQI Menu</h1>",
    unsafe_allow_html=True,
)

# ---------------- MAINTENANCE CHECK ----------------
is_maintenance = get_maintenance_mode()
if is_maintenance:
    if st.session_state.role == "admin":
        st.sidebar.warning("Maintenance Mode is ON")
    else:
        st.markdown(
            """
            <div style='text-align: center; padding: 50px;'>
                <h1>🚧 Under Maintenance</h1>
                <p>The dashboard is currently undergoing scheduled maintenance. Please check back later.</p>
            </div>
        """,
            unsafe_allow_html=True,
        )
        st.stop()


# Theme Toggle
def on_theme_change():
    # Update session state based on toggle
    new_theme = "Dark" if st.session_state.dark_mode_toggle else "Light"
    st.session_state.theme = new_theme
    # Save the selected theme to a cookie that expires in 30 days
    cookie_manager.set(
        "theme",
        new_theme,
        expires_at=pd.Timestamp.now() + pd.Timedelta(days=30),
    )


def on_layout_change():
    # Save layout to cookie
    layout_str = ",".join(st.session_state.layout_select)
    st.session_state.layout_pref = st.session_state.layout_select
    cookie_manager.set(
        "layout",
        layout_str,
        expires_at=pd.Timestamp.now() + pd.Timedelta(days=30),
    )


st.sidebar.toggle(
    "🌙 Dark Mode",
    value=(st.session_state.theme == "Dark"),
    key="dark_mode_toggle",
    on_change=on_theme_change,
)

# Dark Mode CSS & Chart Template
if st.session_state.theme == "Dark":
    st.markdown(
        """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Poppins', sans-serif;
    }

    /* --- ANIMATIONS --- */
    @keyframes gradientBG {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }

    @keyframes fadeIn {
        0% { opacity: 0; transform: translateY(-20px); }
        100% { opacity: 1; transform: translateY(0); }
    }
    
    @keyframes glow {
        from { box-shadow: 0 0 5px #00c9ff, 0 0 10px #00c9ff; }
        to { box-shadow: 0 0 20px #00c9ff, 0 0 30px #00c9ff; }
    }

    @keyframes text-gradient-anim {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }

    /* --- GENERAL STYLES --- */
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(-45deg, #0b0f19, #122a3a, #0b0f19, #23455a);
        background-size: 400% 400%;
        animation: gradientBG 20s ease infinite;
        color: #E0E0E0;
    }

    [data-testid="stSidebar"] {
        background: rgba(0, 0, 0, 0.3);
        backdrop-filter: blur(15px);
        border-right: 1px solid rgba(100, 200, 255, 0.2);
    }

    /* --- TEXT & HEADERS --- */
    .stMarkdown, p, label, span, div {
       color: #EAEAEA !important;
    }

    h1, h2, h3, h4, h5, h6 {
        color: #FFFFFF !important;
    }

    .gradient-text {
        background: linear-gradient(90deg, #1ddde8, #00c9ff, #92fe9d, #ff89b5);
        background-size: 300% 300%;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        animation: text-gradient-anim 8s ease infinite;
        font-weight: 700;
    }
    
    /* Apply to main dashboard title */
    h1.gradient-text {
        font-size: 2.5rem;
    }
    
    /* Apply to subheaders */
    h3.gradient-text {
        border-left: 4px solid #00c9ff;
        padding-left: 15px;
        margin-bottom: 20px;
        background: linear-gradient(90deg, #00c9ff, #92fe9d);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    /* --- WIDGETS --- */
    .metric-container {
        background: rgba(10, 20, 30, 0.5);
        border-radius: 15px;
        padding: 20px;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        backdrop-filter: blur(10px);
        text-align: center;
        transition: transform 0.3s ease, box-shadow 0.3s ease;
        border: 1px solid rgba(0, 201, 255, 0.2);
        animation: fadeIn 1s ease-out;
        position: relative;
        overflow: hidden;
    }
    .metric-container:hover {
        transform: translateY(-10px) scale(1.02);
        box-shadow: 0 0 25px rgba(0, 201, 255, 0.5), 0 0 50px rgba(0, 201, 255, 0.3);
        border-color: rgba(0, 201, 255, 0.5);
    }
    .metric-value {
        font-size: 2.8rem;
        font-weight: 700;
        background: linear-gradient(90deg, #ffffff, #92fe9d, #00c9ff);
        background-size: 200% 200%;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        animation: text-gradient-anim 5s ease infinite;
    }
    .metric-label {
        font-size: 1rem;
        color: #bbb !important;
        margin-top: 5px;
        font-weight: 500;
    }

    /* --- BUTTONS --- */
    .stButton>button {
        background: linear-gradient(90deg, #FC466B 0%, #3F5EFB 100%);
        color: #fff !important;
        border: none;
        border-radius: 25px;
        padding: 12px 24px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 1px;
        transition: all 0.4s ease;
        box-shadow: 0 4px 15px rgba(0, 0, 0, 0.4);
        position: relative;
        overflow: hidden;
    }
    .stButton>button:hover {
        transform: translateY(-3px);
        box-shadow: 0 10px 20px rgba(63, 94, 251, 0.4);
        color: #fff !important;
    }
    .stButton>button::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(120deg, transparent, rgba(255, 255, 255, 0.3), transparent);
        transition: all 0.7s;
    }
    .stButton>button:hover::before {
        left: 100%;
    }

    /* --- INPUTS & TABS --- */
    .stTextInput > div > div > input {
        background-color: rgba(0, 0, 0, 0.3);
        color: white;
        border: 1px solid rgba(0, 201, 255, 0.3);
        border-radius: 10px;
        transition: all 0.3s ease;
    }
    .stTextInput > div > div > input:focus {
        border-color: #00c9ff;
        box-shadow: 0 0 15px rgba(0, 201, 255, 0.5);
    }
    
    .stTabs [data-baseweb="tab-list"] {
        gap: 15px;
        background-color: transparent;
        border-bottom: none;
    }
    .stTabs [data-baseweb="tab"] {
        background-color: rgba(0,0,0,0.2);
        border-radius: 10px;
        padding: 10px 20px;
        color: #ccc;
        border: 1px solid rgba(255,255,255,0.1);
        transition: all 0.3s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        background-color: rgba(0,0,0,0.4);
        color: #fff;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(90deg, #FC466B 0%, #3F5EFB 100%);
        color: white;
        box-shadow: 0 5px 15px rgba(63, 94, 251, 0.3);
    }

    /* --- LOGIN PAGE --- */
    .login-container {
        background: rgba(0, 0, 0, 0.4);
        padding: 40px;
        border-radius: 20px;
        backdrop-filter: blur(15px);
        border: 1px solid rgba(0, 201, 255, 0.2);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.5);
        animation: fadeIn 1.5s ease-out;
    }
    .login-container h1 {
        font-size: 2rem;
    }

    </style>
    """,
        unsafe_allow_html=True,
    )
    chart_template = "plotly_dark"
else:
    chart_template = "plotly"

menu_options = [
    "Dashboard",
    "City Comparison",
    "Health Advice",
    "Prediction",
    "News Feed",
    "Report Download",
    "Raw Data",
    "Upload Data",
    "Profile",
    "Feedback",
]
if st.session_state.role == "admin":
    menu_options.append("User Management")

if "nav_selection" not in st.session_state:
    st.session_state.nav_selection = menu_options[0]
if st.session_state.nav_selection not in menu_options:
    st.session_state.nav_selection = menu_options[0]

menu = st.sidebar.radio("Navigation", menu_options, key="nav_selection")
st.sidebar.success(f"Logged in as: {st.session_state.user}")

if st.sidebar.button("Logout"):
    log_user_activity(st.session_state.user, "Logout")
    st.session_state.logged_in = False
    st.session_state.chat_history = []
    st.session_state.chat_open = False
    st.rerun()

# ---------------- FILTERS ----------------
(
    filtered_df,
    selected_cities,
    date_range,
    alert_threshold,
    city_list,
    suggested_city,
    location_text,
) = render_sidebar_filters(df)

# ---------------- LAYOUT CUSTOMIZATION ----------------
st.sidebar.markdown("---")
st.sidebar.markdown("#### 🎨 Dashboard Layout")
available_sections = [
    "Overview",
    "Maps",
    "Trends & Charts",
    "Pollutant Analysis",
    "Deep Dive",
    "Advanced Analytics",
]
# Ensure session state layout only contains valid options
valid_default = [x for x in st.session_state.layout_pref if x in available_sections]
if not valid_default:
    valid_default = available_sections

selected_layout = st.sidebar.multiselect(
    "Customize & Reorder",
    available_sections,
    default=valid_default,
    key="layout_select",
    on_change=on_layout_change,
)

# ---------------- DASHBOARD PAGE ----------------
if menu == "Dashboard":

    if not filtered_df.empty and filtered_df["AQI"].max() > alert_threshold:
        st.error(
            f"**CRITICAL ALERT**: The AQI in the selected region has reached **{filtered_df['AQI'].max()}**, which exceeds your safety threshold of {alert_threshold}. Please take necessary precautions."
        )

    # Chart Config for Downloading Images
    plotly_config = {
        "displayModeBar": True,
        "displaylogo": False,
        "toImageButtonOptions": {
            "format": "png",
            "filename": "aqi_chart",
            "height": 600,
            "width": 800,
            "scale": 2,
        },
    }

    st.markdown(
        """
        <h1 class='gradient-text' style='text-align: left; animation: fadeIn 1s;'>
            Advanced Air Quality Dashboard
        </h1>
    """,
        unsafe_allow_html=True,
    )

    # ---------------- GLOBAL SEARCH BAR ----------------
    col_search, col_btn, col_spacer = st.columns([2, 1, 1])
    with col_search:
        # Filter out already selected cities to avoid duplicates
        available_cities = [""] + [c for c in city_list if c not in selected_cities]
        search_city = st.selectbox(
            "🔍 Global Search: Find & Add City",
            available_cities,
            index=0,
            key="global_city_search",
            help="Select a city to instantly add it to your dashboard view.",
        )
        if search_city:
            st.session_state.selected_cities.append(search_city)
            st.rerun()

    with col_btn:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.button(
            "⚔️ Compare Cities",
            use_container_width=True,
            help="Compare selected cities in detail",
        ):
            st.session_state.nav_selection = "City Comparison"
            st.session_state.comp_cities_multi = selected_cities
            st.rerun()
    # ---------------------------------------------------

    st.write("### Selected Cities:", ", ".join(selected_cities))

    # Excel Export
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        filtered_df.to_excel(writer, index=False, sheet_name="AQI Data")

    st.download_button(
        label="Download Filtered Data (Excel)",
        data=buffer,
        file_name="aqi_dashboard_data.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    # ---------------- RENDER FUNCTIONS ----------------
    def render_overview():
        # Weather Widget
        if selected_cities:
            weather = get_weather_data(selected_cities[0])
            if weather:
                st.info(
                    f"**Real-time Weather in {selected_cities[0]}:** {weather['desc']} | {weather['temp']}°C | {weather['humidity']}% Humidity | {weather['wind']} km/h Wind"
                )

        col1, col2, col3, col4 = st.columns(4)

        def display_metric(col, label, value, icon, delta=None):
            with col:
                delta_html = ""
                if delta is not None:
                    color = (
                        "#ff4b4b" if delta > 0 else "#00c9ff"
                    )  # Red if increase (bad), Blue if decrease (good)
                    arrow = "▲" if delta > 0 else "▼"
                    delta_html = f"<div style='color: {color}; font-size: 0.9rem; margin-top: 5px; font-weight: bold;'>{arrow} {abs(delta):.2f} vs yesterday</div>"

                st.markdown(
                    f"""
                <div class="metric-container">
                    <div class="metric-icon">{icon}</div>
                    <div class="metric-value">{value}</div>
                    <div class="metric-label">{label}</div>
                    {delta_html}
                </div>
                """,
                    unsafe_allow_html=True,
                )

        # Calculate Day-over-Day Change for AQI
        latest_date = filtered_df["Date"].max()
        aqi_delta = None
        current_aqi = round(filtered_df["AQI"].mean(), 2)

        if pd.notna(latest_date):
            prev_date = latest_date - pd.Timedelta(days=1)
            # Get previous day data for the SAME selected cities from the full dataset
            prev_df = df[(df["City"].isin(selected_cities)) & (df["Date"] == prev_date)]
            if not prev_df.empty:
                prev_aqi = prev_df["AQI"].mean()
                aqi_delta = current_aqi - prev_aqi

        display_metric(col1, "Total Records", len(filtered_df), "")
        display_metric(col2, "Average AQI", current_aqi, "", delta=aqi_delta)
        display_metric(col3, "Max AQI", round(filtered_df["AQI"].max(), 2), "")
        display_metric(col4, "Average PM2.5", round(filtered_df["PM25"].mean(), 2), "")

    def render_maps():
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Geospatial AQI Evolution (Animated Map)</h3>",
            unsafe_allow_html=True,
        )

        # Prepare data for animation
        map_df = filtered_df.copy()
        map_df = add_coordinates(map_df)
        map_df = map_df.dropna(subset=["Lat", "Lon"])
        # Ensure AQI is numeric and drop rows with missing coordinates or AQI
        map_df["AQI"] = pd.to_numeric(map_df["AQI"], errors="coerce")
        map_df = map_df.dropna(subset=["Lat", "Lon", "AQI"])

        if not map_df.empty:
            map_df["Date_Str"] = map_df["Date"].dt.strftime("%Y-%m-%d")
            map_df = map_df.sort_values("Date")

            fig_anim_map = px.scatter_mapbox(
                map_df,
                lat="Lat",
                lon="Lon",
                size="AQI",
                color="AQI",
                animation_frame="Date_Str",
                hover_name="City",
                color_continuous_scale="RdYlGn_r",
                size_max=40,
                zoom=3.5,
                mapbox_style="carto-positron",
                title="AQI Changes Over Time",
            )
            st.plotly_chart(fig_anim_map, use_container_width=True)
        else:
            st.warning("Not enough location data available for the map.")

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>AQI Time-Lapse (Folium Animation)</h3>",
            unsafe_allow_html=True,
        )

        if not map_df.empty:
            # Prepare features for TimestampedGeoJson
            features = []
            for _, row in map_df.iterrows():
                # Determine color based on AQI
                color = "green"
                if row["AQI"] > 300:
                    color = "red"
                elif row["AQI"] > 200:
                    color = "purple"
                elif row["AQI"] > 100:
                    color = "orange"
                elif row["AQI"] > 50:
                    color = "yellow"

                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [row["Lon"], row["Lat"]],
                    },
                    "properties": {
                        "time": row["Date"].strftime("%Y-%m-%d"),
                        "style": {"color": color},
                        "icon": "circle",
                        "iconstyle": {
                            "fillColor": color,
                            "fillOpacity": 0.8,
                            "stroke": "true",
                            "radius": 10,
                        },
                        "popup": f"{row['City']}: {row['AQI']}",
                    },
                }
                features.append(feature)

            # Create Map
            m_anim = folium.Map(
                location=[20.5937, 78.9629], zoom_start=4, tiles="CartoDB dark_matter"
            )

            TimestampedGeoJson(
                {"type": "FeatureCollection", "features": features},
                period="P1D",
                add_last_point=True,
                auto_play=False,
                loop=False,
                max_speed=1,
                loop_button=True,
                date_options="YYYY-MM-DD",
                time_slider_drag_update=True,
            ).add_to(m_anim)

            st_html(m_anim._repr_html_(), height=500)

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Real-Time Wind Analysis (Speed & Direction)</h3>",
            unsafe_allow_html=True,
        )

        if selected_cities:
            # Create base map centered on the first selected city
            first_city_coords = CITY_COORDINATES.get(
                selected_cities[0], [20.5937, 78.9629]
            )
            wind_map = folium.Map(
                location=first_city_coords, zoom_start=5, tiles="CartoDB dark_matter"
            )

            for city in selected_cities:
                coords = CITY_COORDINATES.get(city)
                if coords:
                    w_data = get_weather_data(city)
                    if w_data:
                        # Add Wind Marker (Arrow)
                        folium.Marker(
                            location=coords,
                            icon=get_wind_arrow_icon(
                                w_data["wind_dir"], w_data["wind"]
                            ),
                            tooltip=f"<b>{city}</b><br>Wind: {w_data['wind']} km/h<br>Dir: {w_data['wind_dir']}°",
                        ).add_to(wind_map)

                        # Add Circle for context
                        folium.CircleMarker(
                            location=coords,
                            radius=10,
                            color="#333",
                            fill=True,
                            fill_opacity=0.4,
                        ).add_to(wind_map)

            st_html(wind_map._repr_html_(), height=500)

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Pollution Density Heatmap</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            # Use the latest date in the filtered dataset for the snapshot
            latest_date_in_view = filtered_df["Date"].max()
            heatmap_df = filtered_df[filtered_df["Date"] == latest_date_in_view]

            # Prepare data: [Lat, Lon, Weight (AQI)]
            heat_data = []
            for _, row in heatmap_df.iterrows():
                coords = CITY_COORDINATES.get(row["City"])
                if coords and pd.notnull(row["AQI"]):
                    heat_data.append([coords[0], coords[1], row["AQI"]])

            if heat_data:
                # Center map on the first data point
                start_loc = [heat_data[0][0], heat_data[0][1]]
                m_heat = folium.Map(
                    location=start_loc, zoom_start=5, tiles="CartoDB dark_matter"
                )
                HeatMap(
                    heat_data,
                    radius=25,
                    blur=15,
                    gradient={0.4: "blue", 0.65: "lime", 1: "red"},
                ).add_to(m_heat)
                st_html(m_heat._repr_html_(), height=500)
            else:
                st.info("Insufficient data for heatmap visualization.")

    def render_trends():
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>AQI Trend (Multi City Comparison)</h3>",
            unsafe_allow_html=True,
        )
        fig_line = px.line(filtered_df, x="Date", y="AQI", color="City", markers=True)
        st.plotly_chart(fig_line, use_container_width=True, config=plotly_config)

        # Feature: Download Chart as HTML
        buffer_html = io.StringIO()
        fig_line.write_html(buffer_html)
        html_bytes = buffer_html.getvalue().encode()

        st.download_button(
            label="Download Interactive Chart (HTML)",
            data=html_bytes,
            file_name="aqi_trend_chart.html",
            mime="text/html",
        )

        # Export Data CSV
        st.download_button(
            label="Download Trend Data (CSV)",
            data=filtered_df.to_csv(index=False).encode("utf-8"),
            file_name="aqi_trend_data.csv",
            mime="text/csv",
        )

        colA, colB = st.columns(2)

        with colA:
            st.markdown(
                "<h3 class='gradient-text'>AQI Category Distribution</h3>",
                unsafe_allow_html=True,
            )
            fig_pie = px.pie(filtered_df, names="AQI_Category")
            st.plotly_chart(fig_pie, use_container_width=True, config=plotly_config)

        with colB:
            st.markdown(
                "<h3 class='gradient-text'>Average AQI by City</h3>",
                unsafe_allow_html=True,
            )
            avg_city = filtered_df.groupby("City")["AQI"].mean().reset_index()
            fig_bar = px.bar(avg_city, x="City", y="AQI", text_auto=True)
            st.plotly_chart(fig_bar, use_container_width=True, config=plotly_config)

    def render_pollutant_analysis():
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Pollutant Heatmap (City vs Pollutants)</h3>",
            unsafe_allow_html=True,
        )
        pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
        heatmap_data = filtered_df.groupby("City")[pollutants].mean()
        fig_heat = px.imshow(heatmap_data, text_auto=True)
        st.plotly_chart(fig_heat, use_container_width=True, config=plotly_config)

        # Export Heatmap Data
        st.download_button(
            label="Download Pollutant Analysis Data (CSV)",
            data=heatmap_data.to_csv().encode("utf-8"),
            file_name="pollutant_analysis.csv",
            mime="text/csv",
        )

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Pollutant Correlation Matrix</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            # Select numeric columns for correlation
            corr_cols = ["PM25", "PM10", "NO2", "SO2", "CO", "O3", "AQI"]
            # Filter only columns that exist in the dataframe
            valid_cols = [c for c in corr_cols if c in filtered_df.columns]

            if len(valid_cols) > 1:
                corr_matrix = filtered_df[valid_cols].corr()
                fig_corr = px.imshow(
                    corr_matrix,
                    text_auto=True,
                    color_continuous_scale="RdBu_r",
                    title="Correlation between Pollutants & AQI",
                    template=chart_template,
                )
                st.plotly_chart(
                    fig_corr, use_container_width=True, config=plotly_config
                )

    def render_deep_dive():
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>PM2.5 vs AQI Relationship</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            fig_scatter = px.scatter(
                filtered_df,
                x="PM25",
                y="AQI",
                color="AQI_Category",
                hover_data=["City", "Date"],
                title="Impact of PM2.5 on AQI Levels",
                template=chart_template,
            )
            st.plotly_chart(fig_scatter, use_container_width=True, config=plotly_config)

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>AQI Distribution by City (Box Plot)</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            fig_box = px.box(
                filtered_df,
                x="City",
                y="AQI",
                color="City",
                title="AQI Distribution & Variability",
                template=chart_template,
            )
            st.plotly_chart(fig_box, use_container_width=True, config=plotly_config)

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>AQI Frequency Histogram</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            fig_hist = px.histogram(
                filtered_df,
                x="AQI",
                color="City",
                title="Distribution of AQI Values",
                template=chart_template,
            )
            st.plotly_chart(fig_hist, use_container_width=True, config=plotly_config)

        # Export Deep Dive Data
        if not filtered_df.empty:
            st.download_button(
                label="Download Deep Dive Data (CSV)",
                data=filtered_df.to_csv(index=False).encode("utf-8"),
                file_name="deep_dive_data.csv",
                mime="text/csv",
            )

    def render_advanced_analytics():
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Seasonal Decomposition of AQI</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            city_for_decomposition = st.selectbox(
                "Select City for Seasonal Decomposition",
                filtered_df["City"].unique(),
                key="decomposition_city",
            )
            decomposition_df = filtered_df[
                filtered_df["City"] == city_for_decomposition
            ].set_index("Date")

            if not decomposition_df.empty:
                # Ensure the DataFrame has a DatetimeIndex
                if not isinstance(decomposition_df.index, pd.DatetimeIndex):
                    decomposition_df.index = pd.to_datetime(decomposition_df.index)

                # Sort DataFrame by date
                decomposition_df = decomposition_df.sort_index()

                # Perform seasonal decomposition using moving averages
                rolling_window = 30  # Adjust as needed
                decomposition = (
                    decomposition_df["AQI"]
                    .rolling(window=rolling_window, center=True)
                    .mean()
                )

                # Create plot
                fig_seasonal = px.line(
                    x=decomposition_df.index,
                    y=decomposition,
                    title=f"Seasonal Decomposition of AQI in {city_for_decomposition}",
                    labels={"x": "Date", "y": "Trend"},
                )
                st.plotly_chart(
                    fig_seasonal, use_container_width=True, config=plotly_config
                )

                # Export Seasonal Data
                st.download_button(
                    label="Download Seasonal Trend (CSV)",
                    data=pd.DataFrame(
                        {"Date": decomposition_df.index, "Trend": decomposition}
                    )
                    .to_csv(index=False)
                    .encode("utf-8"),
                    file_name=f"seasonal_trend_{city_for_decomposition}.csv",
                    mime="text/csv",
                )
            else:
                st.warning(
                    "No data available for the selected city to perform seasonal decomposition."
                )
        else:
            st.warning("No data available. Please select cities and a date range.")

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Anomaly Detection (Spikes & Dips)</h3>",
            unsafe_allow_html=True,
        )

        if not filtered_df.empty:
            col_anom1, col_anom2 = st.columns(2)
            with col_anom1:
                # Select city for analysis
                anomaly_city = st.selectbox(
                    "Select City for Anomaly Detection",
                    selected_cities if selected_cities else city_list,
                    key="anomaly_city",
                )
            with col_anom2:
                anomaly_method = st.selectbox(
                    "Detection Method",
                    [
                        "Statistical (Rolling Mean)",
                        "Machine Learning (Isolation Forest)",
                    ],
                    key="anomaly_method",
                )

            # Prepare data
            anom_df = df[df["City"] == anomaly_city].sort_values("Date").copy()

            if anomaly_method == "Statistical (Rolling Mean)":
                # Calculate Rolling Stats (e.g., 7-day window)
                window = 7
                anom_df["Rolling_Mean"] = anom_df["AQI"].rolling(window=window).mean()
                anom_df["Rolling_Std"] = anom_df["AQI"].rolling(window=window).std()

                # Define Bounds
                anom_df["Upper_Bound"] = anom_df["Rolling_Mean"] + (
                    2 * anom_df["Rolling_Std"]
                )
                anom_df["Lower_Bound"] = anom_df["Rolling_Mean"] - (
                    2 * anom_df["Rolling_Std"]
                )

                # Identify Anomalies
                anom_df["Anomaly"] = (anom_df["AQI"] > anom_df["Upper_Bound"]) | (
                    anom_df["AQI"] < anom_df["Lower_Bound"]
                )
                title_text = f"AQI Anomalies in {anomaly_city} (Rolling Mean ± 2σ)"
            else:
                # Machine Learning: Isolation Forest
                # Fill NaNs for ML model
                X_anom = anom_df[["AQI"]].fillna(anom_df["AQI"].mean())
                iso_forest = IsolationForest(contamination=0.05, random_state=42)
                anom_df["Anomaly_Score"] = iso_forest.fit_predict(X_anom)
                anom_df["Anomaly"] = anom_df["Anomaly_Score"] == -1
                title_text = f"AQI Anomalies in {anomaly_city} (Isolation Forest)"

            anomalies = anom_df[anom_df["Anomaly"]]

            # Plot
            fig_anom = px.line(
                anom_df,
                x="Date",
                y="AQI",
                title=title_text,
                template=chart_template,
            )
            fig_anom.add_scatter(
                x=anomalies["Date"],
                y=anomalies["AQI"],
                mode="markers",
                name="Anomaly",
                marker=dict(color="red", size=10, symbol="x"),
            )
            st.plotly_chart(fig_anom, use_container_width=True, config=plotly_config)

            if not anomalies.empty:
                st.warning(f"Detected {len(anomalies)} anomalies in {anomaly_city}.")
                with st.expander("View Anomaly Data"):
                    cols_to_show = ["Date", "AQI"]
                    if "Rolling_Mean" in anom_df.columns:
                        cols_to_show.extend(["Rolling_Mean", "Rolling_Std"])
                    st.dataframe(anomalies[cols_to_show])

                # Export Anomalies
                st.download_button(
                    label="Download Detected Anomalies (CSV)",
                    data=anomalies.to_csv(index=False).encode("utf-8"),
                    file_name=f"anomalies_{anomaly_city}.csv",
                    mime="text/csv",
                )
            else:
                st.success(f"No significant anomalies detected in {anomaly_city}.")

        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>AQI Intensity Calendar</h3>",
            unsafe_allow_html=True,
        )

        # Calendar View Logic
        cal_city_options = selected_cities if selected_cities else city_list
        if cal_city_options:
            cal_city = st.selectbox(
                "Select City for Calendar View", cal_city_options, key="cal_city_select"
            )

            # Use full dataset 'df' to show full year history, ignoring dashboard date filter
            cal_df = df[df["City"] == cal_city].copy()

            if not cal_df.empty:
                cal_df["Year"] = cal_df["Date"].dt.year
                cal_df["Week"] = cal_df["Date"].dt.isocalendar().week
                cal_df["Weekday"] = cal_df["Date"].dt.day_name()

                available_years = sorted(cal_df["Year"].unique())
                if available_years:
                    selected_year = st.selectbox(
                        "Select Year",
                        available_years,
                        index=len(available_years) - 1,
                        key="cal_year_select",
                    )
                    cal_df_year = cal_df[cal_df["Year"] == selected_year]

                    weekday_order = [
                        "Monday",
                        "Tuesday",
                        "Wednesday",
                        "Thursday",
                        "Friday",
                        "Saturday",
                        "Sunday",
                    ]
                    heatmap_data_cal = cal_df_year.pivot_table(
                        index="Weekday", columns="Week", values="AQI", aggfunc="mean"
                    )
                    heatmap_data_cal = heatmap_data_cal.reindex(weekday_order)

                    fig_cal = px.imshow(
                        heatmap_data_cal,
                        labels=dict(x="Week of Year", y="Day of Week", color="AQI"),
                        title=f"AQI Intensity Calendar - {cal_city} ({selected_year})",
                        color_continuous_scale="RdYlGn_r",  # Green (Good) to Red (Bad)
                        template=chart_template,
                    )
                    fig_cal.update_layout(height=400)
                    st.plotly_chart(
                        fig_cal, use_container_width=True, config=plotly_config
                    )

    # ---------------- EXECUTE LAYOUT ----------------
    renderers = {
        "Overview": render_overview,
        "Maps": render_maps,
        "Trends & Charts": render_trends,
        "Pollutant Analysis": render_pollutant_analysis,
        "Deep Dive": render_deep_dive,
        "Advanced Analytics": render_advanced_analytics,
    }

    for section in selected_layout:
        if section in renderers:
            renderers[section]()

# ---------------- CITY COMPARISON PAGE ----------------
elif menu == "City Comparison":
    st.markdown(
        "<h1 class='gradient-text'>Multi-City Comparison</h1>", unsafe_allow_html=True
    )

    # Allow selecting multiple cities
    comp_cities = st.multiselect(
        "Select Cities to Compare",
        city_list,
        default=city_list[:2] if len(city_list) >= 2 else city_list,
        key="comp_cities_multi",
    )

    if comp_cities:
        # Filter data for selected cities
        comp_df = df[df["City"].isin(comp_cities)]

        # Date Filter Application
        if len(date_range) == 2:
            start_date, end_date = date_range
            comp_df = comp_df[
                (comp_df["Date"] >= pd.to_datetime(start_date))
                & (comp_df["Date"] <= pd.to_datetime(end_date))
            ]

        # Metrics Summary
        st.markdown("### Average AQI Summary")
        avg_data = comp_df.groupby("City")["AQI"].mean().reset_index()

        # Display metrics in columns if few cities, else dataframe
        if len(comp_cities) <= 4:
            cols = st.columns(len(comp_cities))
            for idx, row in avg_data.iterrows():
                with cols[idx]:
                    st.metric(row["City"], round(row["AQI"], 2))
        else:
            st.dataframe(avg_data.set_index("City").T)

        st.write("---")

        st.markdown(
            "<h3 class='gradient-text'>AQI Trend Comparison</h3>",
            unsafe_allow_html=True,
        )
        fig_comp = px.line(
            comp_df,
            x="Date",
            y="AQI",
            color="City",
            title="AQI Trend Comparison",
            template=chart_template,
        )
        st.plotly_chart(fig_comp, use_container_width=True)

        st.markdown(
            "<h3 class='gradient-text'>Pollutant Comparison</h3>",
            unsafe_allow_html=True,
        )
        pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
        p_data = comp_df.groupby("City")[pollutants].mean().reset_index()
        p_data = pd.melt(
            p_data, id_vars=["City"], var_name="Pollutant", value_name="Concentration"
        )

        fig_bar = px.bar(
            p_data,
            x="Pollutant",
            y="Concentration",
            color="City",
            barmode="group",
            template=chart_template,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        # Download Comparison Data
        csv_comp = comp_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download Comparison Data (CSV)",
            data=csv_comp,
            file_name="city_comparison_data.csv",
            mime="text/csv",
        )

        # Map Visualization
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>Geographical Comparison</h3>",
            unsafe_allow_html=True,
        )

        map_data = []
        for idx, row in avg_data.iterrows():
            coords = CITY_COORDINATES.get(row["City"], [None, None])
            if coords[0] is not None:
                map_data.append(
                    {
                        "City": row["City"],
                        "Lat": coords[0],
                        "Lon": coords[1],
                        "AQI": row["AQI"],
                    }
                )

        if map_data:
            map_df = pd.DataFrame(map_data)
            fig_map = px.scatter_mapbox(
                map_df,
                lat="Lat",
                lon="Lon",
                size="AQI",
                color="City",
                zoom=4,
                mapbox_style="open-street-map",
                title="City Locations & AQI Severity",
                size_max=30,
                template=chart_template,
            )
            st.plotly_chart(fig_map, use_container_width=True)
        else:
            st.warning("Coordinates not available for selected cities to display map.")

        # ---------------- GLOBAL AVERAGE COMPARISON ----------------
        st.write("---")
        st.markdown(
            "<h3 class='gradient-text'>City vs Global Average</h3>",
            unsafe_allow_html=True,
        )

        # Calculate Global Average (Mean of all AQI records in DB)
        global_aqi_avg = df["AQI"].mean()

        # Select City for Comparison
        comp_city_single = st.selectbox(
            "Select City to Compare with Global Average",
            city_list,
            key="global_comp_city",
        )

        if comp_city_single:
            city_aqi_avg = df[df["City"] == comp_city_single]["AQI"].mean()

            col_g1, col_g2, col_g3 = st.columns(3)
            col_g1.metric(f"{comp_city_single} Average", round(city_aqi_avg, 2))
            col_g2.metric("Global Average", round(global_aqi_avg, 2))
            col_g3.metric(
                "Difference",
                round(city_aqi_avg - global_aqi_avg, 2),
                delta=round(city_aqi_avg - global_aqi_avg, 2),
                delta_color="inverse",
            )

            # Visualization
            comp_data_global = pd.DataFrame(
                {
                    "Region": [comp_city_single, "Global Average"],
                    "AQI": [city_aqi_avg, global_aqi_avg],
                }
            )

            fig_global = px.bar(
                comp_data_global,
                x="Region",
                y="AQI",
                color="Region",
                title=f"AQI Comparison: {comp_city_single} vs Global Average",
                text_auto=True,
                template=chart_template,
            )
            st.plotly_chart(fig_global, use_container_width=True)

# ---------------- HEALTH ADVICE PAGE ----------------
elif menu == "Health Advice":
    st.markdown(
        "<h1 class='gradient-text'>Health Advice & Recommendations</h1>",
        unsafe_allow_html=True,
    )

    h_city = st.selectbox("Select City for Health Advice", city_list)

    if h_city:
        # Get latest data for the city
        city_df = df[df["City"] == h_city].sort_values("Date")
        if not city_df.empty:
            latest_aqi = city_df.iloc[-1]["AQI"]
            cat = aqi_category(latest_aqi)

            st.metric(
                label=f"Current AQI in {h_city}",
                value=latest_aqi,
                delta=cat,
                delta_color="inverse",
            )

            st.subheader(f"Status: {cat}")

            advice_dict = {
                "Good": (
                    "**Enjoy your outdoor activities!**",
                    "Air quality is considered satisfactory, and air pollution poses little or no risk.",
                ),
                "Satisfactory": (
                    "**Sensitive groups should take care.**",
                    "Air quality is acceptable; however, for some pollutants there may be a moderate health concern for a very small number of people who are unusually sensitive to air pollution.",
                ),
                "Moderate": (
                    "**Limit prolonged outdoor exertion.**",
                    "Active children and adults, and people with respiratory disease, such as asthma, should limit prolonged outdoor exertion.",
                ),
                "Poor": (
                    "**Avoid long outdoor activities.**",
                    "Everyone may begin to experience health effects; members of sensitive groups may experience more serious health effects.",
                ),
                "Very Poor": (
                    "**Health warnings of emergency conditions.**",
                    "The entire population is more likely to be affected. Avoid all outdoor physical activities.",
                ),
                "Severe": (
                    "**Health Alert: Serious effects.**",
                    "Everyone may experience more serious health effects. Remain indoors and keep activity levels low.",
                ),
            }

            advice = advice_dict.get(cat, ("Unknown Status", "No advice available."))

            st.markdown(f"### {advice[0]}")
            st.info(advice[1])

            st.write("---")
            st.write("#### General Precautions:")
            if latest_aqi > 200:
                st.write("- Wear an N95 mask if you must go outside.")
                st.write("- Keep windows and doors closed.")
                st.write("- Use an air purifier indoors if available.")
            elif latest_aqi > 100:
                st.write("- Reduce intensity of outdoor exercise.")
                st.write("- Children and elderly should take extra breaks.")
            else:
                st.write("- It is a great day to be outside!")
# ---------------- PREDICTION PAGE ----------------
elif menu == "Prediction":

    st.markdown(
        "<h1 class='gradient-text'>AQI Prediction (Machine Learning)</h1>",
        unsafe_allow_html=True,
    )

    # ---------------- PREPARE DATA ----------------
    train_df = df[["PM25", "PM10", "NO2", "SO2", "CO", "O3", "AQI"]].dropna()

    X = train_df[["PM25", "PM10", "NO2", "SO2", "CO", "O3"]]
    y = train_df["AQI"]

    from sklearn.model_selection import train_test_split
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    import numpy as np

    # ---------------- SPLIT DATA ----------------
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    # ---------------- TRAIN MODEL ----------------
    model = LinearRegression()
    model.fit(X_train, y_train)

    # ---------------- PREDICT TEST ----------------
    y_pred = model.predict(X_test)

    # ---------------- EVALUATION ----------------
    mae = mean_absolute_error(y_test, y_pred)
    mse = mean_squared_error(y_test, y_pred)
    rmse = np.sqrt(mse)
    r2 = r2_score(y_test, y_pred)

    # ---------------- SHOW PERFORMANCE ----------------
    st.markdown(
        "<h3 class='gradient-text'>Model Performance</h3>", unsafe_allow_html=True
    )
    st.write(f"R² Score: {r2:.2f}")
    st.write(f"MAE: {mae:.2f}")
    st.write(f"RMSE: {rmse:.2f}")

    st.write("---")

    # ---------------- USER INPUT ----------------
    col1, col2, col3 = st.columns(3)

    with col1:
        pm25 = st.number_input("PM2.5", value=50.0)
        pm10 = st.number_input("PM10", value=80.0)

    with col2:
        no2 = st.number_input("NO2", value=20.0)
        so2 = st.number_input("SO2", value=10.0)

    with col3:
        co = st.number_input("CO", value=1.0)
        o3 = st.number_input("O3", value=30.0)

    # ---------------- PREDICTION BUTTON ----------------
    if st.button("Predict AQI"):

        prediction = model.predict([[pm25, pm10, no2, so2, co, o3]])
        pred_val = round(prediction[0], 2)

        st.success(f"Predicted AQI = {pred_val}")

        # AQI Category Function
        def aqi_category(aqi):
            if aqi <= 50:
                return "Good"
            elif aqi <= 100:
                return "Moderate"
            elif aqi <= 200:
                return "Unhealthy"
            elif aqi <= 300:
                return "Very Unhealthy"
            else:
                return "Hazardous"

        st.info(f"AQI Category: {aqi_category(pred_val)}")
# ---------------- NEWS FEED PAGE ----------------
elif menu == "News Feed":
    st.markdown(
        "<h1 class='gradient-text'>Global Air Quality News</h1>", unsafe_allow_html=True
    )
    st.write(
        "Latest updates on air pollution, environmental policies, and health advisories."
    )

    api_key = get_secret("NEWS_API_KEY")
    if not api_key:
        st.warning("News API Key is missing.")
        st.markdown("Add your API key in Streamlit Secrets.")
        st.markdown("[Get a free API Key from NewsAPI.org](https://newsapi.org/)")
        st.stop()

    news_items = fetch_aqi_news(api_key)

    if not news_items:
        st.info("No news articles found at the moment.")
    else:
        for article in news_items[:10]:
            with st.container():
                col_img, col_text = st.columns([1, 3])

                with col_img:
                    if article.get("urlToImage"):
                        st.image(article["urlToImage"], use_container_width=True)
                    else:
                        st.markdown("*No Image*")

                with col_text:
                    st.subheader(
                        f"[{article.get('title','No Title')}]({article.get('url','#')})"
                    )
                    st.caption(
                        f"Source: {article.get('source',{}).get('name','Unknown')} | "
                        f"Published: {article.get('publishedAt','')[:10]}"
                    )
                    st.write(article.get("description", "No description available."))

            st.write("---")
# ---------------- REPORT DOWNLOAD PAGE ----------------
elif menu == "Report Download":
    st.markdown(
        "<h1 class='gradient-text'>Download AQI Report (PDF)</h1>",
        unsafe_allow_html=True,
    )
    if st.button("Generate PDF Report"):

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        pdf.cell(200, 10, txt="Air Quality Dashboard Report", ln=True, align="C")
        pdf.ln(10)

        pdf.cell(200, 10, txt=f"Cities: {', '.join(selected_cities)}", ln=True)
        pdf.cell(200, 10, txt=f"Total Records: {len(filtered_df)}", ln=True)
        pdf.cell(
            200, 10, txt=f"Average AQI: {round(filtered_df['AQI'].mean(),2)}", ln=True
        )
        pdf.cell(
            200, 10, txt=f"Maximum AQI: {round(filtered_df['AQI'].max(),2)}", ln=True
        )

        pdf.output("aqi_report.pdf")

        with open("aqi_report.pdf", "rb") as file:
            st.download_button(
                label="Download PDF",
                data=file.read(),
                file_name="aqi_report.pdf",
                mime="application/pdf",
            )


# ---------------- RAW DATA PAGE ----------------
elif menu == "Raw Data":
    st.markdown(
        "<h1 class='gradient-text'>Raw Data Viewer</h1>", unsafe_allow_html=True
    )
    st.dataframe(filtered_df)

# ---------------- UPLOAD DATA PAGE ----------------
elif menu == "Upload Data":
    st.markdown(
        "<h1 class='gradient-text'>Upload & Analyze Your Data</h1>",
        unsafe_allow_html=True,
    )
    st.write(
        "Upload your own air quality data (CSV or Excel) to visualize trends and detect anomalies."
    )

    uploaded_file = st.file_uploader("Choose a file", type=["csv", "xlsx"])

    if uploaded_file:
        try:
            if uploaded_file.name.endswith(".csv"):
                user_df = pd.read_csv(uploaded_file)
            else:
                user_df = pd.read_excel(uploaded_file)

            st.write("### Data Preview")
            st.dataframe(user_df.head())

            # Column Selection
            cols = user_df.columns.tolist()
            col_u1, col_u2 = st.columns(2)

            with col_u1:
                date_col = st.selectbox(
                    "Select Date Column", cols, index=0 if "Date" in cols else 0
                )
            with col_u2:
                val_col = st.selectbox(
                    "Select Value Column (e.g., AQI)",
                    cols,
                    index=1 if len(cols) > 1 else 0,
                )

            if st.button("Analyze Uploaded Data"):
                # Convert date
                user_df[date_col] = pd.to_datetime(user_df[date_col], errors="coerce")
                user_df = user_df.dropna(subset=[date_col, val_col])
                user_df = user_df.sort_values(date_col)

                # Plot Trend
                st.markdown(
                    f"<h3 class='gradient-text'>{val_col} Trend Analysis</h3>",
                    unsafe_allow_html=True,
                )
                fig_user = px.line(
                    user_df, x=date_col, y=val_col, title=f"{val_col} over Time"
                )
                st.plotly_chart(fig_user, use_container_width=True)

                # Stats
                st.write("### Statistics")
                st.write(user_df[val_col].describe())

        except Exception as e:
            st.error(f"Error processing file: {e}")

# ---------------- FEEDBACK PAGE ----------------
elif menu == "Feedback":
    st.markdown(
        "<h1 class='gradient-text'>Report Air Quality Issues</h1>",
        unsafe_allow_html=True,
    )
    st.write("Help us improve by reporting local air quality issues in your area.")

    with st.form("feedback_form"):
        f_city = st.selectbox("Select City", city_list)
        f_issue = st.selectbox(
            "Issue Type",
            [
                "Smog/Haze",
                "Bad Odor",
                "Dust",
                "Smoke",
                "Industrial Emissions",
                "Vehicle Pollution",
                "Other",
            ],
        )
        f_desc = st.text_area(
            "Description (Optional)", placeholder="Describe the issue in detail..."
        )

        submitted = st.form_submit_button("Submit Report")

        if submitted:
            conn = sqlite3.connect("aqi.db")
            c = conn.cursor()
            c.execute(
                "INSERT INTO feedback (username, city, issue_type, description) VALUES (?, ?, ?, ?)",
                (st.session_state.user, f_city, f_issue, f_desc),
            )
            conn.commit()
            conn.close()
            log_user_activity(st.session_state.user, f"Submitted feedback for {f_city}")
            st.success("Thank you! Your feedback has been recorded.")

# ---------------- PROFILE PAGE ----------------
elif menu == "Profile":
    st.markdown("<h1 class='gradient-text'>My Profile</h1>", unsafe_allow_html=True)
    st.write(f"**Username:** {st.session_state.user}")
    st.write(f"**Role:** {st.session_state.role}")

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>Profile Picture</h3>", unsafe_allow_html=True
    )

    conn = sqlite3.connect("aqi.db")
    c = conn.cursor()
    c.execute(
        "SELECT profile_pic FROM users WHERE username=?", (st.session_state.user,)
    )
    pic_data = c.fetchone()
    conn.close()

    if pic_data and pic_data[0]:
        st.image(pic_data[0], width=150, caption="Your Profile Picture")

    uploaded_pic = st.file_uploader(
        "Upload New Profile Picture", type=["jpg", "png", "jpeg"]
    )
    if uploaded_pic and st.button("Save Profile Picture"):
        pic_bytes = uploaded_pic.read()
        conn = sqlite3.connect("aqi.db")
        c = conn.cursor()
        c.execute(
            "UPDATE users SET profile_pic=? WHERE username=?",
            (pic_bytes, st.session_state.user),
        )
        conn.commit()
        conn.close()
        log_user_activity(st.session_state.user, "Updated Profile Picture")
        st.success("Profile picture updated successfully!")
        st.rerun()

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>Notification Settings</h3>", unsafe_allow_html=True
    )

    conn = sqlite3.connect("aqi.db")
    c = conn.cursor()
    c.execute(
        "SELECT subscription FROM users WHERE username=?", (st.session_state.user,)
    )
    sub_status = c.fetchone()
    is_subscribed = bool(sub_status[0]) if sub_status else False
    conn.close()

    new_sub = st.checkbox(
        "Subscribe to Daily AQI Email Reports (09:00 AM)", value=is_subscribed
    )
    if new_sub != is_subscribed:
        conn = sqlite3.connect("aqi.db")
        c = conn.cursor()
        c.execute(
            "UPDATE users SET subscription=? WHERE username=?",
            (1 if new_sub else 0, st.session_state.user),
        )
        conn.commit()
        conn.close()
        st.session_state.daily_report_sub = new_sub
        st.success("Subscription settings updated!")

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>Change Password</h3>", unsafe_allow_html=True
    )

    current_pw = st.text_input("Current Password", type="password")
    new_pw = st.text_input("New Password", type="password")
    confirm_pw = st.text_input("Confirm New Password", type="password")

    if st.button("Update Password"):
        if new_pw != confirm_pw:
            st.error("New passwords do not match!")
        else:
            conn = sqlite3.connect("aqi.db")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT password FROM users WHERE username=?", (st.session_state.user,)
            )
            row = cursor.fetchone()

            if row and bcrypt.checkpw(current_pw.encode(), row[0].encode()):
                new_hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
                cursor.execute(
                    "UPDATE users SET password=? WHERE username=?",
                    (new_hashed, st.session_state.user),
                )
                conn.commit()
                log_user_activity(st.session_state.user, "Changed Password")
                st.success("Password updated successfully!")
            else:
                st.error("Incorrect current password.")
            conn.close()

# ---------------- USER MANAGEMENT PAGE ----------------
elif menu == "User Management":
    st.markdown(
        "<h1 class='gradient-text'>User Management (Admin Only)</h1>",
        unsafe_allow_html=True,
    )

    conn = sqlite3.connect("aqi.db")
    # Fetching only ID and Username for security (hiding hashed passwords)
    users_df = pd.read_sql_query("SELECT id, username FROM users", conn)
    conn.close()

    st.dataframe(users_df, use_container_width=True)
    st.info(f"Total Registered Users: {len(users_df)}")

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>System Settings</h3>", unsafe_allow_html=True
    )
    m_mode = st.toggle("Maintenance Mode", value=get_maintenance_mode())
    if m_mode != get_maintenance_mode():
        set_maintenance_mode(m_mode)
        log_user_activity(
            st.session_state.user, f"Toggled Maintenance Mode to {m_mode}"
        )
        st.rerun()

    st.write("---")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            "<h3 class='gradient-text'>Delete User</h3>", unsafe_allow_html=True
        )
        if not users_df.empty:
            user_to_delete = st.selectbox(
                "Select User to Delete", users_df["username"].tolist()
            )

            if st.button("Delete Selected User"):
                conn = sqlite3.connect("aqi.db")
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE username=?", (user_to_delete,))
                conn.commit()
                conn.close()
                log_user_activity(
                    st.session_state.user, f"Deleted user: {user_to_delete}"
                )
                st.success(f"User '{user_to_delete}' has been deleted.")
                st.rerun()
        else:
            st.warning("No users to delete.")

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>Update User Password</h3>", unsafe_allow_html=True
    )

    u_update = st.selectbox(
        "Select User to Update", users_df["username"].tolist(), key="update_user_select"
    )
    new_pw_admin = st.text_input("New Password", type="password", key="new_pw_admin")

    if st.button("Update Password"):
        if new_pw_admin:
            conn = sqlite3.connect("aqi.db")
            cursor = conn.cursor()
            hashed_pw = bcrypt.hashpw(new_pw_admin.encode(), bcrypt.gensalt()).decode()
            cursor.execute(
                "UPDATE users SET password=? WHERE username=?", (hashed_pw, u_update)
            )
            conn.commit()
            conn.close()
            log_user_activity(
                st.session_state.user, f"Admin changed password for: {u_update}"
            )
            st.success(f"Password for {u_update} updated successfully!")
        else:
            st.warning("Please enter a new password.")

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>Manage User Roles</h3>", unsafe_allow_html=True
    )

    col_role1, col_role2 = st.columns(2)
    with col_role1:
        u_role_select = st.selectbox(
            "Select User", users_df["username"].tolist(), key="role_user"
        )
    with col_role2:
        new_role_select = st.selectbox(
            "Select New Role", ["user", "admin"], key="role_select"
        )

    if st.button("Update Role"):
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE users SET role=? WHERE username=?", (new_role_select, u_role_select)
        )
        conn.commit()
        conn.close()
        log_user_activity(
            st.session_state.user,
            f"Changed role of {u_role_select} to {new_role_select}",
        )
        st.success(f"Role for {u_role_select} updated to {new_role_select}.")
        st.rerun()

    with col2:
        st.markdown(
            "<h3 class='gradient-text'>Reset Database</h3>", unsafe_allow_html=True
        )
        with st.expander("Danger Zone: Clear All Users"):
            st.warning("This action will permanently delete ALL registered users!")
            if st.button("DELETE ALL USERS", type="primary"):
                conn = sqlite3.connect("aqi.db")
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users")
                conn.commit()
                conn.close()
                log_user_activity(st.session_state.user, "RESET ALL USERS DATABASE")
                st.error("All users have been deleted from the database.")
                st.rerun()

    st.write("---")
    st.markdown("<h3 class='gradient-text'>Activity Logs</h3>", unsafe_allow_html=True)
    conn = sqlite3.connect("aqi.db")
    logs_df = pd.read_sql_query(
        "SELECT * FROM activity_logs ORDER BY timestamp DESC", conn
    )
    conn.close()

    # Improved UI for Logs
    with st.container(height=400):
        for index, row in logs_df.iterrows():
            st.markdown(
                f"**{row['timestamp']}** - `{row['username']}`: {row['action']}"
            )

    csv = logs_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download Logs as CSV",
        data=csv,
        file_name="activity_logs.csv",
        mime="text/csv",
    )

    st.write("---")
    st.markdown(
        "<h3 class='gradient-text'>User Feedback Reports</h3>", unsafe_allow_html=True
    )
    conn = sqlite3.connect("aqi.db")
    feedback_df = pd.read_sql_query(
        "SELECT * FROM feedback ORDER BY timestamp DESC", conn
    )
    conn.close()

    # Filter by Status
    status_filter = st.radio(
        "Filter Status:", ["All", "Pending", "Resolved"], horizontal=True
    )
    if status_filter != "All":
        feedback_df = feedback_df[feedback_df["status"] == status_filter]

    st.dataframe(feedback_df, use_container_width=True)

    selected_feedback_id = st.selectbox(
        "Select Feedback to View Details",
        feedback_df["id"].tolist(),
        key="selected_feedback",
    )

    if selected_feedback_id:
        st.write("#### Feedback Details")
        selected_feedback = feedback_df[feedback_df["id"] == selected_feedback_id].iloc[
            0
        ]

        st.write(f"**ID:** {selected_feedback['id']}")
        st.write(f"**Username:** {selected_feedback['username']}")
        st.write(f"**City:** {selected_feedback['city']}")
        st.write(f"**Issue Type:** {selected_feedback['issue_type']}")
        st.write(f"**Description:** {selected_feedback['description']}")
        st.write(f"**Status:** {selected_feedback['status']}")
        st.write(f"**Timestamp:** {selected_feedback['timestamp']}")

        st.write("---")
        st.write("#### Associated Activity Log")
        activity_log = get_activity_log_for_feedback(selected_feedback_id)
        st.write(activity_log)
    # Feedback Resolution
    if "status" in feedback_df.columns:
        pending_feedback = feedback_df[feedback_df["status"] == "Pending"]

        if not pending_feedback.empty:
            st.markdown("#### Resolve Feedback")
            f_ids_to_resolve = st.multiselect(
                "Select Feedback IDs to Resolve", pending_feedback["id"].tolist()
            )

            if st.button("Mark Selected as Resolved"):
                if f_ids_to_resolve:
                    conn = sqlite3.connect("aqi.db")
                    cursor = conn.cursor()
                    placeholders = ", ".join("?" for _ in f_ids_to_resolve)
                    query = f"UPDATE feedback SET status='Resolved' WHERE id IN ({placeholders})"
                    cursor.execute(query, f_ids_to_resolve)
                    conn.commit()
                    conn.close()
                    log_user_activity(
                        st.session_state.user,
                        f"Bulk resolved feedback IDs: {f_ids_to_resolve}",
                    )
                    st.success(f"Feedback IDs {f_ids_to_resolve} marked as Resolved!")
                    st.rerun()
                else:
                    st.warning("Please select at least one feedback item.")
        elif not feedback_df.empty:
            st.info("All feedback items are resolved!")

# ==========================================================
# ---------------- FLOATING CHATBOT (BOTTOM RIGHT) ----------
# ==========================================================

st.markdown(
    """
<style>
#chat-btn {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: #2E86C1;
    color: white;
    padding: 12px 18px;
    border-radius: 50px;
    font-weight: bold;
    cursor: pointer;
    z-index: 9999;
    text-align:center;
    box-shadow: 0px 4px 12px rgba(0,0,0,0.3);
}
.chat-popup {
    position: fixed;
    bottom: 80px;
    right: 20px;
    width: 360px;
    background: white;
    border-radius: 15px;
    box-shadow: 0px 4px 20px rgba(0,0,0,0.25);
    z-index: 9999;
    padding: 15px;
}
.chat-title {
    font-size: 16px;
    font-weight: bold;
    color: #2E86C1;
}
.chat-location {
    font-size: 12px;
    color: gray;
    margin-bottom: 10px;
}
.chat-body {
    height: 240px;
    overflow-y: auto;
    background: #f5f5f5;
    padding: 10px;
    border-radius: 10px;
    font-size: 14px;
}
.user-msg {
    text-align: right;
    margin: 6px 0;
    padding: 8px;
    background: #d6eaf8;
    border-radius: 10px;
}
.bot-msg {
    text-align: left;
    margin: 6px 0;
    padding: 8px;
    background: #d4efdf;
    border-radius: 10px;
}
</style>
""",
    unsafe_allow_html=True,
)


# Chat button toggle
if st.button("Chat Assistant", key="open_chat"):
    st.session_state.chat_open = not st.session_state.chat_open


# Chat popup UI
if st.session_state.chat_open:

    st.markdown(
        f"""
    <div class="chat-popup">
        <div class="chat-title">AQI Assistant</div>
        <div class="chat-location">{location_text}</div>
    """,
        unsafe_allow_html=True,
    )

    # Chat History
    chat_html = '<div class="chat-body">'
    for chat in st.session_state.chat_history:
        safe_content = html.escape(chat["content"])
        if chat["role"] == "user":
            chat_html += f'<div class="user-msg">{safe_content}</div>'
        else:
            chat_html += f'<div class="bot-msg">{safe_content}</div>'
    chat_html += "</div>"

    st.markdown(chat_html, unsafe_allow_html=True)

    # Quick Buttons
    colq1, colq2, colq3 = st.columns(3)

    if colq1.button("AQI Today"):
        st.session_state.chat_history.append(
            {"role": "user", "content": "What is the AQI today and is it safe?"}
        )
        st.rerun()

    if colq2.button("Mask?"):
        st.session_state.chat_history.append(
            {"role": "user", "content": "Do I need to wear a mask today?"}
        )
        st.rerun()

    if colq3.button("Exercise"):
        st.session_state.chat_history.append(
            {"role": "user", "content": "Is it safe to do outdoor exercise today?"}
        )
        st.rerun()

    # Input
    col_chat_in, col_chat_mic = st.columns([5, 1])

    with col_chat_in:
        user_msg = st.text_input(
            "Type your message",
            key="chat_input_msg",
            label_visibility="collapsed",
            placeholder="Type or speak...",
        )

    with col_chat_mic:
        # Voice Input Button
        voice_text = speech_to_text(
            language="en",
            start_prompt="Speak",
            stop_prompt="Stop",
            just_once=True,
            key="STT",
        )

    # Handle Input (Text Button or Voice)
    send_clicked = st.button("Send", key="send_btn", use_container_width=True)
    final_msg = (
        voice_text
        if voice_text
        else (user_msg if send_clicked and user_msg.strip() else None)
    )

    if final_msg:
        st.session_state.chat_history.append({"role": "user", "content": final_msg})

        context_data = "No specific city data available."
        if suggested_city and suggested_city in df["City"].values:
            city_df = df[df["City"] == suggested_city].sort_values("Date")
            if not city_df.empty:
                latest = city_df.iloc[-1]
                avg_aqi = city_df["AQI"].mean()

                context_data = f"""
                Target City: {suggested_city}
                Latest Record Date: {latest['Date'].strftime('%Y-%m-%d')}
                Current AQI: {latest['AQI']} (Category: {latest['AQI_Category']})
                Pollutant Levels:
                - PM2.5: {latest['PM25']}
                - PM10: {latest['PM10']}
                - NO2: {latest['NO2']}
                - SO2: {latest['SO2']}
                - CO: {latest['CO']}
                - O3: {latest['O3']}

                Historical Average AQI: {round(avg_aqi, 2)}
                """

        prompt = f"""
        You are an advanced Air Quality Health & Data Assistant.
        
        [REAL-TIME DATA CONTEXT]
        {context_data}
        
        [USER QUESTION]
        {final_msg}
        
        [INSTRUCTIONS]
        1.  Analyze the provided pollutant levels (PM2.5, PM10, etc.) to give specific advice.
        2. Provide a direct, helpful answer to the user's question.
        3. If AQI is high (>100), strictly recommend health precautions (masks, air purifiers).
        4. Keep the response concise and professional.
        """

        if not client:
            ai_reply = (
                "OpenAI API key missing. Add `OPENAI_API_KEY` to Streamlit secrets."
            )
        else:
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6,
                    max_tokens=200,  # Limit the response length for better control
                )

                ai_reply = response.choices[0].message.content

            except Exception as e:
                ai_reply = f"OpenAI Error: {e}"

        st.session_state.chat_history.append({"role": "assistant", "content": ai_reply})

        st.rerun()

    if st.button("Close Chat", key="close_chat"):
        st.session_state.chat_open = False
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
