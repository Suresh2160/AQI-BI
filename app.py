import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from sklearn.linear_model import LinearRegression
from fpdf import FPDF
import numpy as np
import requests
import bcrypt
from openai import OpenAI
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import smtplib
from email.message import EmailMessage
from prophet import Prophet
from prophet.plot import plot_plotly
import plotly.graph_objects as go
import schedule
import time
import threading

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="AQI Dashboard", page_icon="🌍", layout="wide")

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

if "theme" not in st.session_state:
    st.session_state.theme = "Light"


# ---------------- OPENAI CLIENT ----------------
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])


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
def get_weather_data(city):
    try:
        # Using wttr.in for weather data (JSON format)
        url = f"https://wttr.in/{city}?format=j1"
        response = requests.get(url, timeout=2)
        data = response.json()
        current = data['current_condition'][0]
        return {
            "temp": current['temp_C'],
            "humidity": current['humidity'],
            "desc": current['weatherDesc'][0]['value'],
            "wind": current['windspeedKmph']
        }
    except:
        return None


# ---------------- USER DATABASE (Signup/Login) ----------------
def init_user_db():
    conn = sqlite3.connect("aqi.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'user'
        )
    """)
    
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
            cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", ("admin", admin_pw, "admin"))
        except sqlite3.IntegrityError:
            pass

    # Migration: Add profile_pic column if it doesn't exist
    try:
        cursor.execute("SELECT profile_pic FROM users LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE users ADD COLUMN profile_pic BLOB")

    # Create Activity Logs Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            action TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Create Feedback Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            city TEXT,
            issue_type TEXT,
            description TEXT,
            status TEXT DEFAULT 'Pending',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: Add status column to feedback if it doesn't exist
    try:
        cursor.execute("SELECT status FROM feedback LIMIT 1")
    except sqlite3.OperationalError:
        cursor.execute("ALTER TABLE feedback ADD COLUMN status TEXT DEFAULT 'Pending'")

    # Create Forecasts Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            forecast_date DATE,
            predicted_aqi REAL,
            lower_bound REAL,
            upper_bound REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

# ---------------- BACKGROUND JOB (Daily Forecasts) ----------------
def generate_daily_forecasts():
    """Generates forecasts for all cities for the next 7 days."""
    try:
        print("🔄 Starting Daily Forecast Job...")
        conn = sqlite3.connect("aqi.db")
        # Get list of cities from existing data
        cities_df = pd.read_sql_query("SELECT DISTINCT City FROM air_quality", conn)
        cities = cities_df["City"].tolist()
        
        # Load data for training
        full_df = pd.read_sql_query("SELECT City, Date, AQI FROM air_quality", conn)
        full_df["Date"] = pd.to_datetime(full_df["Date"], errors="coerce")
        
        cursor = conn.cursor()
        
        for city in cities:
            p_df = full_df[full_df["City"] == city][["Date", "AQI"]].dropna()
            if len(p_df) > 20:
                p_df = p_df.rename(columns={"Date": "ds", "AQI": "y"}).sort_values("ds")
                m = Prophet()
                m.fit(p_df)
                future = m.make_future_dataframe(periods=7) # Forecast next 7 days
                forecast = m.predict(future)
                
                # Save last 7 days (future)
                future_data = forecast.tail(7)
                for _, row in future_data.iterrows():
                    cursor.execute("""
                        INSERT INTO forecasts (city, forecast_date, predicted_aqi, lower_bound, upper_bound) 
                        VALUES (?, ?, ?, ?, ?)
                    """, (city, row['ds'].date(), row['yhat'], row['yhat_lower'], row['yhat_upper']))
        
        conn.commit()
        conn.close()
        print("✅ Daily Forecast Job Completed.")
    except Exception as e:
        print(f"❌ Error in Daily Forecast Job: {e}")

@st.cache_resource
def start_background_scheduler():
    schedule.every().day.at("00:00").do(generate_daily_forecasts)
    def run_schedule():
        while True:
            schedule.run_pending()
            time.sleep(60)
    t = threading.Thread(target=run_schedule, daemon=True)
    t.start()
    return t

# Start the background scheduler
start_background_scheduler()

def log_user_activity(username, action):
    try:
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO activity_logs (username, action) VALUES (?, ?)", (username, action))
        conn.commit()
        conn.close()
    except:
        pass

def get_activity_log_for_feedback(feedback_id):
    try:
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT activity_logs.* FROM activity_logs
            JOIN feedback ON activity_logs.username = feedback.username
            WHERE feedback.id = ?
        """, (feedback_id,))
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
        cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (username, hashed_pw, "user"))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def login_user(username, password):
    conn = sqlite3.connect("aqi.db")
    cursor = conn.cursor()

    cursor.execute("SELECT password, role FROM users WHERE username=?", (username,))
    row = cursor.fetchone()
    conn.close()

    if row:
        stored_hash = row[0].encode()
        if bcrypt.checkpw(password.encode(), stored_hash):
            return True, row[1] # Return Success, Role
    return False, None


def login_google_user(token):
    try:
        # Replace with your actual Google Client ID
        CLIENT_ID = "YOUR_GOOGLE_CLIENT_ID"
        id_info = id_token.verify_oauth2_token(token, google_requests.Request(), CLIENT_ID)
        email = id_info['email']

        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute("SELECT username, role FROM users WHERE username=?", (email,))
        row = cursor.fetchone()
        
        if not row:
            # Register new Google user with a placeholder password
            cursor.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (email, "GOOGLE_AUTH", "user"))
            conn.commit()
            role = "user"
        else:
            role = row[1]
        conn.close()
        return email, role
    except ValueError:
        return None, None


def send_reset_email(to_email):
    # ---------------- CONFIG ----------------
    # Replace these with your actual email credentials
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    SENDER_EMAIL = "your_email@gmail.com"
    SENDER_PASSWORD = "your_app_password"
    # ----------------------------------------

    msg = EmailMessage()
    msg.set_content(f"Hello,\n\nWe received a request to reset your password.\nClick the link below to proceed:\nhttp://localhost:8501/?reset_token=dummy_token_for_{to_email}\n\nIf you did not request this, please ignore this email.")
    msg['Subject'] = "AQI Dashboard - Password Reset Request"
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email

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


# ---------------- AUTH UI ----------------
if st.session_state.logged_in == False:

    # Check for Google Login Token in URL (passed from login.html)
    if "token" in st.query_params:
        token = st.query_params["token"]
        email, role = login_google_user(token)
        if email:
            st.session_state.logged_in = True
            st.session_state.user = email
            st.session_state.role = role
            log_user_activity(email, "Login via Google")
            st.success(f"Login Successful with Google: {email} ✅")
            st.rerun()

    # Check for Password Reset Request (passed from forgot_password.html)
    if "reset_email" in st.query_params:
        r_email = st.query_params["reset_email"]
        if send_reset_email(r_email):
            st.success(f"✅ Password reset link has been sent to: {r_email}")
        else:
            st.error("❌ Failed to send email. Please check server logs or SMTP configuration.")

    # Check for HTML Form Login/Signup (via URL parameters)
    if "username" in st.query_params and "password" in st.query_params:
        qp = st.query_params
        username = qp["username"]
        password = qp["password"]

        # If 'email' is present, treat as Registration (from reg.html)
        if "email" in qp:
            if signup_user(username, password):
                st.session_state.logged_in = True
                st.session_state.user = username
                st.session_state.role = "user"
                log_user_activity(username, "New User Signup via HTML")
                st.success("Account Created! Logging you in...")
                st.query_params.clear() # Clean URL
                st.rerun()
            else:
                st.error("Username already exists. Please try a different one.")
        
        # Otherwise, treat as Login (from login.html)
        else:
            success, role = login_user(username, password)
            if success:
                st.session_state.logged_in = True
                st.session_state.user = username
                st.session_state.role = role
                log_user_activity(username, "Login via Password")
                st.success("Login Successful ✅")
                st.query_params.clear() # Clean URL
                st.rerun()
            else:
                st.error("Invalid username or password ❌")

    # If still not logged in, show access denied
    if not st.session_state.logged_in:
        st.markdown("<h1 style='text-align:center;'>🌍 AQI Dashboard System</h1>", unsafe_allow_html=True)
        st.warning("Please log in using the external Login Page.")
        st.markdown(f"""
            <div style="text-align: center;">
                <p>Open <b>login.html</b> or <b>reg.html</b> in your browser to authenticate.</p>
            </div>
        """, unsafe_allow_html=True)
        st.stop()


# ---------------- SIDEBAR MENU ----------------
st.sidebar.title("🌍 AQI Dashboard Menu")

# Theme Toggle
st.sidebar.selectbox("🎨 Theme", ["Light", "Dark"], key="theme")

# Dark Mode CSS & Chart Template
if st.session_state.theme == "Dark":
    st.markdown("""
    <style>
    [data-testid="stAppViewContainer"] { background-color: #0E1117; color: white; }
    [data-testid="stSidebar"] { background-color: #262730; }
    [data-testid="stHeader"] { background-color: rgba(0,0,0,0); }
    .stMarkdown, p, h1, h2, h3, h4, h5, h6, label, span { color: white !important; }
    div[data-testid="stMetricValue"] { color: white !important; }
    </style>
    """, unsafe_allow_html=True)
    chart_template = "plotly_dark"
else:
    chart_template = "plotly"

menu_options = ["Dashboard", "City Comparison", "Health Advice", "Travel Recommendation", "Prediction", "Forecast Manager", "Report Download", "Raw Data", "Profile", "Feedback"]
if st.session_state.role == "admin":
    menu_options.append("User Management")

menu = st.sidebar.radio("Navigation", menu_options)
st.sidebar.success(f"Logged in as: {st.session_state.user}")

if st.sidebar.button("🚪 Logout"):
    log_user_activity(st.session_state.user, "Logout")
    st.session_state.logged_in = False
    st.session_state.chat_history = []
    st.session_state.chat_open = False
    st.rerun()

# ---------------- FILTERS ----------------
st.sidebar.header("🔍 Filters")

city_list = sorted(df["City"].dropna().unique())

# 📍 Manual location selection
st.sidebar.subheader("📍 Select Location")
manual_city = st.sidebar.selectbox("Choose your City", city_list, key="manual_city")

suggested_city = manual_city
location_text = manual_city

# Multi-city selection
selected_cities = st.sidebar.multiselect(
    "Select Cities",
    city_list,
    default=city_list[:3],
    key="selected_cities"
)

# Date filter
min_date = df["Date"].min()
max_date = df["Date"].max()

date_range = st.sidebar.date_input(
    "Select Date Range",
    [min_date, max_date],
    key="date_range"
)

# Alert Threshold
st.sidebar.header("🔔 Alerts")
alert_threshold = st.sidebar.slider("AQI Alert Threshold", 50, 500, 200, 10)

# Apply filters
filtered_df = df[df["City"].isin(selected_cities)]

if len(date_range) == 2:
    start_date, end_date = date_range
    filtered_df = filtered_df[
        (filtered_df["Date"] >= pd.to_datetime(start_date)) &
        (filtered_df["Date"] <= pd.to_datetime(end_date))
    ]

# Check for Alerts
if not filtered_df.empty:
    max_aqi_view = filtered_df["AQI"].max()
    if max_aqi_view > alert_threshold:
        st.toast(f"🚨 High AQI Alert: {max_aqi_view} detected!", icon="⚠️")
        st.sidebar.error(f"⚠️ Alert: AQI > {alert_threshold} detected!")

# ---------------- DASHBOARD PAGE ----------------
if menu == "Dashboard":

    if not filtered_df.empty and filtered_df["AQI"].max() > alert_threshold:
        st.error(f"🚨 **CRITICAL ALERT**: The AQI in the selected region has reached **{filtered_df['AQI'].max()}**, which exceeds your safety threshold of {alert_threshold}. Please take necessary precautions.")

    st.title("📊 Advanced Air Quality Dashboard")
    st.write("### 📌 Selected Cities:", ", ".join(selected_cities))

    # Weather Widget
    if selected_cities:
        weather = get_weather_data(selected_cities[0])
        if weather:
            st.info(f"☁️ **Real-time Weather in {selected_cities[0]}:** {weather['desc']} | 🌡️ {weather['temp']}°C | 💧 {weather['humidity']}% Humidity | 💨 {weather['wind']} km/h Wind")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Records", len(filtered_df))
    col2.metric("Average AQI", round(filtered_df["AQI"].mean(), 2))
    col3.metric("Max AQI", round(filtered_df["AQI"].max(), 2))
    col4.metric("Average PM2.5", round(filtered_df["PM25"].mean(), 2))

    st.write("---")

    st.subheader("📈 AQI Trend (Multi City Comparison)")
    fig_line = px.line(filtered_df, x="Date", y="AQI", color="City", markers=True)
    st.plotly_chart(fig_line, use_container_width=True)

    colA, colB = st.columns(2)

    with colA:
        st.subheader("🟢 AQI Category Distribution")
        fig_pie = px.pie(filtered_df, names="AQI_Category")
        st.plotly_chart(fig_pie, use_container_width=True)

    with colB:
        st.subheader("🏙️ Average AQI by City")
        avg_city = filtered_df.groupby("City")["AQI"].mean().reset_index()
        fig_bar = px.bar(avg_city, x="City", y="AQI", text_auto=True)
        st.plotly_chart(fig_bar, use_container_width=True)

    st.write("---")

    st.subheader("🔥 Pollutant Heatmap (City vs Pollutants)")
    pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
    heatmap_data = filtered_df.groupby("City")[pollutants].mean()
    fig_heat = px.imshow(heatmap_data, text_auto=True)
    st.plotly_chart(fig_heat, use_container_width=True)

    st.write("---")
    st.subheader("🔗 Pollutant Correlation Matrix")
    
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
                template=chart_template
            )
            st.plotly_chart(fig_corr, use_container_width=True)

    st.write("---")
    st.subheader("📉 PM2.5 vs AQI Relationship")
    
    if not filtered_df.empty:
        fig_scatter = px.scatter(
            filtered_df, 
            x="PM25", 
            y="AQI", 
            color="AQI_Category",
            hover_data=["City", "Date"],
            title="Impact of PM2.5 on AQI Levels",
            template=chart_template
        )
        st.plotly_chart(fig_scatter, use_container_width=True)

    st.write("---")
    st.subheader("📦 AQI Distribution by City (Box Plot)")
    
    if not filtered_df.empty:
        fig_box = px.box(
            filtered_df, 
            x="City", 
            y="AQI", 
            color="City",
            title="AQI Distribution & Variability",
            template=chart_template
        )
        st.plotly_chart(fig_box, use_container_width=True)

    st.write("---")
    st.subheader("📊 AQI Frequency Histogram")

    if not filtered_df.empty:
        fig_hist = px.histogram(
            filtered_df,
            x="AQI",
            color="City",
            title="Distribution of AQI Values",
            template=chart_template
        )
        st.plotly_chart(fig_hist, use_container_width=True)

    st.write("---")
    st.subheader("Seasonal Decomposition of AQI")

    if not filtered_df.empty:
        city_for_decomposition = st.selectbox("Select City for Seasonal Decomposition", filtered_df["City"].unique(), key="decomposition_city")
        decomposition_df = filtered_df[filtered_df["City"] == city_for_decomposition].set_index("Date")

        if not decomposition_df.empty:
            # Ensure the DataFrame has a DatetimeIndex
            if not isinstance(decomposition_df.index, pd.DatetimeIndex):
                decomposition_df.index = pd.to_datetime(decomposition_df.index)

            # Sort DataFrame by date
            decomposition_df = decomposition_df.sort_index()

            # Perform seasonal decomposition using moving averages
            rolling_window = 30  # Adjust as needed
            decomposition = decomposition_df["AQI"].rolling(window=rolling_window, center=True).mean()

            # Create plot
            fig_seasonal = px.line(x=decomposition_df.index, y=decomposition, title=f"Seasonal Decomposition of AQI in {city_for_decomposition}", labels={"x": "Date", "y": "Trend"})
            st.plotly_chart(fig_seasonal, use_container_width=True)
        else:
            st.warning("No data available for the selected city to perform seasonal decomposition.")
    else:
        st.warning("No data available. Please select cities and a date range.")

    st.write("---")
    st.subheader("🚨 Anomaly Detection (Spikes & Dips)")

    if not filtered_df.empty:
        # Select city for analysis
        anomaly_city = st.selectbox("Select City for Anomaly Detection", selected_cities if selected_cities else city_list, key="anomaly_city")
        
        # Prepare data
        anom_df = df[df["City"] == anomaly_city].sort_values("Date").copy()
        
        # Calculate Rolling Stats (e.g., 7-day window)
        window = 7
        anom_df["Rolling_Mean"] = anom_df["AQI"].rolling(window=window).mean()
        anom_df["Rolling_Std"] = anom_df["AQI"].rolling(window=window).std()
        
        # Define Bounds
        anom_df["Upper_Bound"] = anom_df["Rolling_Mean"] + (2 * anom_df["Rolling_Std"])
        anom_df["Lower_Bound"] = anom_df["Rolling_Mean"] - (2 * anom_df["Rolling_Std"])
        
        # Identify Anomalies
        anom_df["Anomaly"] = ((anom_df["AQI"] > anom_df["Upper_Bound"]) | (anom_df["AQI"] < anom_df["Lower_Bound"]))
        anomalies = anom_df[anom_df["Anomaly"]]
        
        # Plot
        fig_anom = px.line(anom_df, x="Date", y="AQI", title=f"AQI Anomalies in {anomaly_city} (Rolling Mean ± 2σ)", template=chart_template)
        fig_anom.add_scatter(x=anomalies["Date"], y=anomalies["AQI"], mode='markers', name='Anomaly', marker=dict(color='red', size=10, symbol='x'))
        st.plotly_chart(fig_anom, use_container_width=True)
        
        if not anomalies.empty:
            st.warning(f"⚠️ Detected {len(anomalies)} anomalies in {anomaly_city}.")
            with st.expander("View Anomaly Data"):
                st.dataframe(anomalies[["Date", "AQI", "Rolling_Mean", "Rolling_Std"]])
        else:
            st.success(f"✅ No significant anomalies detected in {anomaly_city} based on current trends.")




    st.write("---")
    st.subheader("� AQI Intensity Calendar")
    
    # Calendar View Logic
    cal_city_options = selected_cities if selected_cities else city_list
    if cal_city_options:
        cal_city = st.selectbox("Select City for Calendar View", cal_city_options, key="cal_city_select")
        
        # Use full dataset 'df' to show full year history, ignoring dashboard date filter
        cal_df = df[df["City"] == cal_city].copy()
        
        if not cal_df.empty:
            cal_df["Year"] = cal_df["Date"].dt.year
            cal_df["Week"] = cal_df["Date"].dt.isocalendar().week
            cal_df["Weekday"] = cal_df["Date"].dt.day_name()
            
            available_years = sorted(cal_df["Year"].unique())
            if available_years:
                selected_year = st.selectbox("Select Year", available_years, index=len(available_years)-1, key="cal_year_select")
                cal_df_year = cal_df[cal_df["Year"] == selected_year]
                
                weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                heatmap_data_cal = cal_df_year.pivot_table(index="Weekday", columns="Week", values="AQI", aggfunc="mean")
                heatmap_data_cal = heatmap_data_cal.reindex(weekday_order)
                
                fig_cal = px.imshow(
                    heatmap_data_cal,
                    labels=dict(x="Week of Year", y="Day of Week", color="AQI"),
                    title=f"AQI Intensity Calendar - {cal_city} ({selected_year})",
                    color_continuous_scale="RdYlGn_r", # Green (Good) to Red (Bad)
                    template=chart_template
                )
                fig_cal.update_layout(height=400)
                st.plotly_chart(fig_cal, use_container_width=True)

# ---------------- CITY COMPARISON PAGE ----------------
elif menu == "City Comparison":
    st.title("⚔️ Multi-City Comparison")
    
    # Allow selecting multiple cities
    comp_cities = st.multiselect("Select Cities to Compare", city_list, default=city_list[:2] if len(city_list) >= 2 else city_list, key="comp_cities_multi")

    if comp_cities:
        # Filter data for selected cities
        comp_df = df[df["City"].isin(comp_cities)]
        
        # Date Filter Application
        if len(date_range) == 2:
            start_date, end_date = date_range
            comp_df = comp_df[(comp_df["Date"] >= pd.to_datetime(start_date)) & (comp_df["Date"] <= pd.to_datetime(end_date))]

        # Metrics Summary
        st.write("### 📊 Average AQI Summary")
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
        
        st.subheader("📈 AQI Trend Comparison")
        fig_comp = px.line(comp_df, x="Date", y="AQI", color="City", title="AQI Trend Comparison", template=chart_template)
        st.plotly_chart(fig_comp, use_container_width=True)
        
        st.subheader("📊 Pollutant Comparison")
        pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
        p_data = comp_df.groupby("City")[pollutants].mean().reset_index()
        p_data = pd.melt(p_data, id_vars=["City"], var_name="Pollutant", value_name="Concentration")
        
        fig_bar = px.bar(p_data, x="Pollutant", y="Concentration", color="City", barmode="group", template=chart_template)
        st.plotly_chart(fig_bar, use_container_width=True)

        # Download Comparison Data
        csv_comp = comp_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Comparison Data (CSV)",
            data=csv_comp,
            file_name='city_comparison_data.csv',
            mime='text/csv',
        )

        # Map Visualization
        st.write("---")
        st.subheader("🗺️ Geographical Comparison")
        
        # Coordinates for major Indian cities (expand as needed)
        city_coordinates = {
            "Ahmedabad": [23.0225, 72.5714], "Aizawl": [23.7271, 92.7176], "Amaravati": [16.5417, 80.5158],
            "Amritsar": [31.6340, 74.8723], "Bengaluru": [12.9716, 77.5946], "Bhopal": [23.2599, 77.4126],
            "Brajrajnagar": [21.8333, 83.9167], "Chandigarh": [30.7333, 76.7794], "Chennai": [13.0827, 80.2707],
            "Coimbatore": [11.0168, 76.9558], "Delhi": [28.6139, 77.2090], "Ernakulam": [9.9816, 76.2999],
            "Gurugram": [28.4595, 77.0266], "Guwahati": [26.1445, 91.7362], "Hyderabad": [17.3850, 78.4867],
            "Jaipur": [26.9124, 75.7873], "Jorapokhar": [23.7000, 86.4100], "Kochi": [9.9312, 76.2673],
            "Kolkata": [22.5726, 88.3639], "Lucknow": [26.8467, 80.9462], "Mumbai": [19.0760, 72.8777],
            "Patna": [25.5941, 85.1376], "Shillong": [25.5788, 91.8933], "Talcher": [20.9500, 85.2167],
            "Thiruvananthapuram": [8.5241, 76.9366], "Visakhapatnam": [17.6868, 83.2185]
        }

        map_data = []
        for idx, row in avg_data.iterrows():
            coords = city_coordinates.get(row["City"], [None, None])
            if coords[0] is not None:
                map_data.append({"City": row["City"], "Lat": coords[0], "Lon": coords[1], "AQI": row["AQI"]})
        
        if map_data:
            map_df = pd.DataFrame(map_data)
            fig_map = px.scatter_mapbox(map_df, lat="Lat", lon="Lon", size="AQI", color="City", 
                                        zoom=4, mapbox_style="open-street-map", 
                                        title="City Locations & AQI Severity",
                                        size_max=30, template=chart_template)
            st.plotly_chart(fig_map, use_container_width=True)
        else:
            st.warning("Coordinates not available for selected cities to display map.")

        # Anomaly Rate Comparison
        st.write("---")
        st.subheader("🚨 Anomaly Rate Comparison")
        
        anomaly_data = []
        for city in comp_cities:
            city_df = df[df["City"] == city].sort_values("Date")
            if not city_df.empty:
                # Rolling stats (same logic as Dashboard)
                window = 7
                city_df["Rolling_Mean"] = city_df["AQI"].rolling(window=window).mean()
                city_df["Rolling_Std"] = city_df["AQI"].rolling(window=window).std()
                city_df["Upper"] = city_df["Rolling_Mean"] + (2 * city_df["Rolling_Std"])
                city_df["Lower"] = city_df["Rolling_Mean"] - (2 * city_df["Rolling_Std"])
                
                anomalies = city_df[(city_df["AQI"] > city_df["Upper"]) | (city_df["AQI"] < city_df["Lower"])]
                rate = (len(anomalies) / len(city_df)) * 100
                anomaly_data.append({"City": city, "Anomaly Rate (%)": rate, "Count": len(anomalies)})
        
        if anomaly_data:
            anom_comp_df = pd.DataFrame(anomaly_data)
            fig_anom_comp = px.bar(anom_comp_df, x="City", y="Anomaly Rate (%)", color="City", 
                                   text_auto=True, title="Percentage of Anomalous Days by City", template=chart_template)
            st.plotly_chart(fig_anom_comp, use_container_width=True)

# ---------------- HEALTH ADVICE PAGE ----------------
elif menu == "Health Advice":
    st.title("❤️ Health Advice & Recommendations")
    
    h_city = st.selectbox("Select City for Health Advice", city_list)
    
    if h_city:
        # Get latest data for the city
        city_df = df[df["City"] == h_city].sort_values("Date")
        if not city_df.empty:
            latest_aqi = city_df.iloc[-1]["AQI"]
            cat = aqi_category(latest_aqi)
            
            st.metric(label=f"Current AQI in {h_city}", value=latest_aqi, delta=cat, delta_color="inverse")
            
            st.subheader(f"Status: {cat}")
            
            advice_dict = {
                "Good": ("✅ **Enjoy your outdoor activities!**", "Air quality is considered satisfactory, and air pollution poses little or no risk."),
                "Satisfactory": ("⚠️ **Sensitive groups should take care.**", "Air quality is acceptable; however, for some pollutants there may be a moderate health concern for a very small number of people who are unusually sensitive to air pollution."),
                "Moderate": ("😷 **Limit prolonged outdoor exertion.**", "Active children and adults, and people with respiratory disease, such as asthma, should limit prolonged outdoor exertion."),
                "Poor": ("⛔ **Avoid long outdoor activities.**", "Everyone may begin to experience health effects; members of sensitive groups may experience more serious health effects."),
                "Very Poor": ("🚨 **Health warnings of emergency conditions.**", "The entire population is more likely to be affected. Avoid all outdoor physical activities."),
                "Severe": ("☠️ **Health Alert: Serious effects.**", "Everyone may experience more serious health effects. Remain indoors and keep activity levels low.")
            }
            
            advice = advice_dict.get(cat, ("Unknown Status", "No advice available."))
            
            st.markdown(f"### {advice[0]}")
            st.info(advice[1])
            
            st.write("---")
            st.write("#### 🛡️ General Precautions:")
            if latest_aqi > 200:
                st.write("- 😷 Wear an N95 mask if you must go outside.")
                st.write("- 🏠 Keep windows and doors closed.")
                st.write("- 🌬️ Use an air purifier indoors if available.")
            elif latest_aqi > 100:
                st.write("- 🏃 Reduce intensity of outdoor exercise.")
                st.write("- 👶 Children and elderly should take extra breaks.")
            else:
                st.write("- 🌳 It is a great day to be outside!")

# ---------------- TRAVEL RECOMMENDATION PAGE ----------------
elif menu == "Travel Recommendation":
    st.title("✈️ Best City to Visit (AQI Recommender)")
    st.write("Find the best destination with the cleanest air for your travel dates.")

    travel_date = st.date_input("Select Travel Date", min_value=min_date, value=pd.to_datetime("today"))

    if st.button("Find Best Cities"):
        # 1. Check if we have a forecast for this date in the database
        conn = sqlite3.connect("aqi.db")
        forecast_df = pd.read_sql_query("SELECT city, predicted_aqi FROM forecasts WHERE forecast_date = ?", conn, params=(travel_date,))
        conn.close()

        rec_source = ""
        recommendations = pd.DataFrame()

        if not forecast_df.empty:
            rec_source = "Based on AI Forecasts 🤖"
            recommendations = forecast_df.sort_values("predicted_aqi").head(5)
            recommendations.rename(columns={"city": "City", "predicted_aqi": "Expected AQI"}, inplace=True)
        else:
            # 2. Fallback: Use Historical Average for this Month
            rec_source = "Based on Historical Averages (Seasonality) 📅"
            month = travel_date.month
            hist_df = df[df["Date"].dt.month == month]
            if not hist_df.empty:
                avg_aqi = hist_df.groupby("City")["AQI"].mean().reset_index()
                recommendations = avg_aqi.sort_values("AQI").head(5)
                recommendations.rename(columns={"AQI": "Expected AQI"}, inplace=True)
        
        if not recommendations.empty:
            st.success(f"✅ Top Recommendations for {travel_date} ({rec_source})")
            
            cols = st.columns(len(recommendations))
            for idx, (i, row) in enumerate(recommendations.iterrows()):
                with cols[idx]:
                    aqi_val = round(row['Expected AQI'], 1)
                    st.metric(label=row['City'], value=aqi_val, delta=aqi_category(aqi_val), delta_color="inverse")
            
            st.dataframe(recommendations, use_container_width=True)
        else:
            st.warning("Not enough data to generate recommendations.")

# ---------------- PREDICTION PAGE ----------------
elif menu == "Prediction":

    st.title("🤖 AQI Prediction & Forecasting")
    
    tab_pred, tab_forecast = st.tabs(["🧪 Pollutant-based Prediction", "📅 Future Trend Forecasting (Prophet)"])
    tab_pred, tab_forecast, tab_whatif = st.tabs(["🧪 Pollutant-based Prediction", "📅 Future Trend Forecasting (Prophet)", "🎛️ What-If Analysis"])
    
    with tab_pred:
        st.subheader("Predict AQI based on Pollutants (Linear Regression)")
        train_df = df[["PM25", "PM10", "NO2", "SO2", "CO", "O3", "AQI"]].dropna()

        X = train_df[["PM25", "PM10", "NO2", "SO2", "CO", "O3"]]
        y = train_df["AQI"]

        model = LinearRegression()
        model.fit(X, y)

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

        if st.button("🔮 Predict AQI"):
            prediction = model.predict([[pm25, pm10, no2, so2, co, o3]])
            pred_val = round(prediction[0], 2)

            st.success(f"✅ Predicted AQI = {pred_val}")
            st.info(f"📌 AQI Category: {aqi_category(pred_val)}")

    with tab_forecast:
        st.subheader("Forecast Future AQI Trends (Prophet)")
        st.write("Uses historical data to predict future AQI trends.")
        
        p_city = st.selectbox("Select City for Forecasting", city_list, key="prophet_city")
        days_to_predict = st.slider("Days to Forecast", 7, 365, 30)
        
        if st.button("Generate Forecast"):
            with st.spinner("Training Prophet model..."):
                # Prepare data for Prophet (ds, y)
                p_df = df[df["City"] == p_city][["Date", "AQI"]].dropna()
                p_df = p_df.rename(columns={"Date": "ds", "AQI": "y"}).sort_values("ds")
                
                if len(p_df) > 20:
                    try:
                        m = Prophet()
                        m.fit(p_df)
                        future = m.make_future_dataframe(periods=days_to_predict)
                        forecast = m.predict(future)
                        
                        st.session_state['last_forecast'] = {'city': p_city, 'data': forecast, 'days': days_to_predict}
                        
                        st.success(f"Forecast generated for {p_city}!")
                        
                        st.write(f"### 🔮 Forecast for next {days_to_predict} days")
                        fig_prophet = plot_plotly(m, forecast)
                        st.plotly_chart(fig_prophet, use_container_width=True)
                        
                        with st.expander("View Forecast Data"):
                            st.dataframe(forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']].tail(days_to_predict))
                            
                        st.write("#### Trend Components")
                        fig_comp = m.plot_components(forecast)
                        st.pyplot(fig_comp)
                        
                    except Exception as e:
                        st.error(f"Error during forecasting: {e}")
                else:
                    st.warning("Not enough historical data to train the model (need at least 20 points).")

        if 'last_forecast' in st.session_state:
            st.write("---")
            if st.button("💾 Save Forecast to Database"):
                try:
                    f_data = st.session_state['last_forecast']
                    conn = sqlite3.connect("aqi.db")
                    cursor = conn.cursor()
                    
                    # Save only the future predictions
                    future_data = f_data['data'].tail(f_data['days'])
                    
                    for _, row in future_data.iterrows():
                        cursor.execute("""
                            INSERT INTO forecasts (city, forecast_date, predicted_aqi, lower_bound, upper_bound) 
                            VALUES (?, ?, ?, ?, ?)
                        """, (f_data['city'], row['ds'].date(), row['yhat'], row['yhat_lower'], row['yhat_upper']))
                    
                    conn.commit()
                    conn.close()
                    st.success(f"✅ Forecast for {f_data['city']} saved successfully!")
                    log_user_activity(st.session_state.user, f"Saved forecast for {f_data['city']}")
                except Exception as e:
                    st.error(f"Error saving forecast: {e}")

    with tab_whatif:
        st.subheader("🎛️ Interactive What-If Analysis")
        st.write("Adjust pollutant levels to simulate scenarios and see the impact on AQI.")
        
        wi_city = st.selectbox("Select Baseline City", city_list, key="wi_city")
        
        # Train model on full dataset for the What-If tool
        train_df_wi = df[["PM25", "PM10", "NO2", "SO2", "CO", "O3", "AQI"]].dropna()
        X_wi = train_df_wi[["PM25", "PM10", "NO2", "SO2", "CO", "O3"]]
        y_wi = train_df_wi["AQI"]
        model_wi = LinearRegression()
        model_wi.fit(X_wi, y_wi)

        # Get baseline data for selected city
        city_stats = df[df["City"] == wi_city][["PM25", "PM10", "NO2", "SO2", "CO", "O3"]].mean()
        
        col_wi1, col_wi2 = st.columns(2)
        input_vals = []
        pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
        
        for i, pol in enumerate(pollutants):
            base_val = city_stats[pol] if not np.isnan(city_stats[pol]) else 0.0
            with col_wi1 if i < 3 else col_wi2:
                val = st.slider(f"{pol} Level", 0.0, float(base_val * 3) + 50.0, float(base_val), key=f"wi_{pol}")
                input_vals.append(val)
        
        # Real-time Prediction
        wi_pred = model_wi.predict([input_vals])[0]
        
        st.write("---")
        st.metric("Predicted AQI (What-If)", round(wi_pred, 2), delta=round(wi_pred - df[df["City"]==wi_city]["AQI"].mean(), 2), delta_color="inverse")
        st.info(f"📌 Predicted Category: **{aqi_category(wi_pred)}**")

# ---------------- FORECAST MANAGER PAGE ----------------
elif menu == "Forecast Manager":
    st.title("🔮 Forecast Manager & Verification")

    # 1. View/Delete Forecasts
    st.subheader("📂 Saved Forecasts")
    conn = sqlite3.connect("aqi.db")
    forecasts_df = pd.read_sql_query("SELECT * FROM forecasts ORDER BY created_at DESC", conn)
    conn.close()

    st.dataframe(forecasts_df, use_container_width=True)

    if not forecasts_df.empty:
        # Create a readable label for batches (City + Created Date)
        forecasts_df['batch_label'] = forecasts_df['city'] + " (Generated: " + forecasts_df['created_at'] + ")"
        unique_batches = forecasts_df['batch_label'].unique()

        with st.expander("🗑️ Delete Forecasts"):
            batch_to_delete = st.selectbox("Select Forecast Batch to Delete", unique_batches)
            if st.button("Delete Batch"):
                # Filter IDs belonging to this batch
                ids_to_delete = forecasts_df[forecasts_df['batch_label'] == batch_to_delete]['id'].tolist()
                
                conn = sqlite3.connect("aqi.db")
                cursor = conn.cursor()
                placeholders = ', '.join('?' for _ in ids_to_delete)
                cursor.execute(f"DELETE FROM forecasts WHERE id IN ({placeholders})", ids_to_delete)
                conn.commit()
                conn.close()
                log_user_activity(st.session_state.user, f"Deleted forecast batch: {batch_to_delete}")
                st.success("Forecast batch deleted successfully.")
                st.rerun()

        # 2. Compare with Actuals
        st.write("---")
        st.subheader("📉 Compare Forecast vs Actual Data")
        st.write("Verify the accuracy of past forecasts by comparing them with actual recorded AQI data.")

        selected_batch = st.selectbox("Select Forecast to Verify", unique_batches, key="verify_batch")

        # Get forecast data for this batch
        batch_data = forecasts_df[forecasts_df['batch_label'] == selected_batch].copy()
        city_name = batch_data.iloc[0]['city']

        # Prepare Data for Merge
        batch_data['forecast_date'] = pd.to_datetime(batch_data['forecast_date'])
        
        actual_data = df[df['City'] == city_name][['Date', 'AQI']].copy()
        actual_data['Date'] = pd.to_datetime(actual_data['Date'])

        # Merge Forecast with Actuals
        comparison_df = pd.merge(batch_data, actual_data, left_on='forecast_date', right_on='Date', how='inner', suffixes=('_pred', '_actual'))

        if not comparison_df.empty:
            # Calculate Error Metrics
            mae = np.mean(np.abs(comparison_df['predicted_aqi'] - comparison_df['AQI']))
            st.metric("Mean Absolute Error (MAE)", round(mae, 2), help="Lower is better. Represents average deviation from actual AQI.")

            # Plot Comparison
            fig_verify = go.Figure()
            fig_verify.add_trace(go.Scatter(x=comparison_df['forecast_date'], y=comparison_df['AQI'], mode='lines+markers', name='Actual AQI', line=dict(color='green')))
            fig_verify.add_trace(go.Scatter(x=comparison_df['forecast_date'], y=comparison_df['predicted_aqi'], mode='lines+markers', name='Predicted AQI', line=dict(dash='dash', color='blue')))
            fig_verify.add_trace(go.Scatter(x=comparison_df['forecast_date'], y=comparison_df['upper_bound'], mode='lines', name='Upper Bound', line=dict(width=0), showlegend=False))
            fig_verify.add_trace(go.Scatter(x=comparison_df['forecast_date'], y=comparison_df['lower_bound'], mode='lines', name='Lower Bound', line=dict(width=0), fill='tonexty', fillcolor='rgba(0,100,255,0.2)', showlegend=False))
            
            fig_verify.update_layout(title=f"Forecast Verification for {city_name}", template=chart_template, hovermode="x unified")
            st.plotly_chart(fig_verify, use_container_width=True)
        else:
            st.warning(f"No overlapping actual data found for this forecast period in {city_name}. Wait for new data to be recorded.")

# ---------------- REPORT DOWNLOAD PAGE ----------------
elif menu == "Report Download":

    st.title("📄 Download AQI Report (PDF)")

    if st.button("Generate PDF Report"):

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)

        pdf.cell(200, 10, txt="Air Quality Dashboard Report", ln=True, align="C")
        pdf.ln(10)

        pdf.cell(200, 10, txt=f"Cities: {', '.join(selected_cities)}", ln=True)
        pdf.cell(200, 10, txt=f"Total Records: {len(filtered_df)}", ln=True)
        pdf.cell(200, 10, txt=f"Average AQI: {round(filtered_df['AQI'].mean(),2)}", ln=True)
        pdf.cell(200, 10, txt=f"Maximum AQI: {round(filtered_df['AQI'].max(),2)}", ln=True)

        pdf.output("aqi_report.pdf")

        with open("aqi_report.pdf", "rb") as file:
            st.download_button("📥 Download PDF", file, file_name="aqi_report.pdf")


# ---------------- RAW DATA PAGE ----------------
elif menu == "Raw Data":
    st.title("📋 Raw Data Viewer")
    st.dataframe(filtered_df)


# ---------------- FEEDBACK PAGE ----------------
elif menu == "Feedback":
    st.title("📢 Report Air Quality Issues")
    st.write("Help us improve by reporting local air quality issues in your area.")
    
    with st.form("feedback_form"):
        f_city = st.selectbox("Select City", city_list)
        f_issue = st.selectbox("Issue Type", ["Smog/Haze", "Bad Odor", "Dust", "Smoke", "Industrial Emissions", "Vehicle Pollution", "Other"])
        f_desc = st.text_area("Description (Optional)", placeholder="Describe the issue in detail...")
        
        submitted = st.form_submit_button("Submit Report")
        
        if submitted:
            conn = sqlite3.connect("aqi.db")
            c = conn.cursor()
            c.execute("INSERT INTO feedback (username, city, issue_type, description) VALUES (?, ?, ?, ?)", 
                      (st.session_state.user, f_city, f_issue, f_desc))
            conn.commit()
            conn.close()
            log_user_activity(st.session_state.user, f"Submitted feedback for {f_city}")
            st.success("✅ Thank you! Your feedback has been recorded.")

# ---------------- PROFILE PAGE ----------------
elif menu == "Profile":
    st.title("👤 My Profile")
    st.write(f"**Username:** {st.session_state.user}")
    st.write(f"**Role:** {st.session_state.role}")

    st.write("---")
    st.subheader("🖼️ Profile Picture")
    
    conn = sqlite3.connect("aqi.db")
    c = conn.cursor()
    c.execute("SELECT profile_pic FROM users WHERE username=?", (st.session_state.user,))
    pic_data = c.fetchone()
    conn.close()

    if pic_data and pic_data[0]:
        st.image(pic_data[0], width=150, caption="Your Profile Picture")
    
    uploaded_pic = st.file_uploader("Upload New Profile Picture", type=["jpg", "png", "jpeg"])
    if uploaded_pic:
        if st.button("Save Profile Picture"):
            pic_bytes = uploaded_pic.read()
            conn = sqlite3.connect("aqi.db")
            c = conn.cursor()
            c.execute("UPDATE users SET profile_pic=? WHERE username=?", (pic_bytes, st.session_state.user))
            conn.commit()
            conn.close()
            log_user_activity(st.session_state.user, "Updated Profile Picture")
            st.success("Profile picture updated successfully!")
            st.rerun()

    st.write("---")
    st.subheader("🔐 Change Password")

    current_pw = st.text_input("Current Password", type="password")
    new_pw = st.text_input("New Password", type="password")
    confirm_pw = st.text_input("Confirm New Password", type="password")

    if st.button("Update Password"):
        if new_pw != confirm_pw:
            st.error("New passwords do not match!")
        else:
            # Verify current password
            conn = sqlite3.connect("aqi.db")
            cursor = conn.cursor()
            cursor.execute("SELECT password FROM users WHERE username=?", (st.session_state.user,))
            row = cursor.fetchone()

            if row and bcrypt.checkpw(current_pw.encode(), row[0].encode()):
                new_hashed = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
                cursor.execute("UPDATE users SET password=? WHERE username=?", (new_hashed, st.session_state.user))
                conn.commit()
                log_user_activity(st.session_state.user, "Changed Password")
                st.success("Password updated successfully!")
            else:
                st.error("Incorrect current password.")
            conn.close()

# ---------------- USER MANAGEMENT PAGE ----------------
elif menu == "User Management":
    st.title("👤 User Management (Admin Only)")
    
    conn = sqlite3.connect("aqi.db")
    # Fetching only ID and Username for security (hiding hashed passwords)
    users_df = pd.read_sql_query("SELECT id, username FROM users", conn)
    conn.close()
    
    st.dataframe(users_df, use_container_width=True)
    st.info(f"Total Registered Users: {len(users_df)}")

    st.write("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("❌ Delete User")
        if not users_df.empty:
            user_to_delete = st.selectbox("Select User to Delete", users_df["username"].tolist())
            
            if st.button("Delete Selected User"):
                conn = sqlite3.connect("aqi.db")
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE username=?", (user_to_delete,))
                conn.commit()
                conn.close()
                log_user_activity(st.session_state.user, f"Deleted user: {user_to_delete}")
                st.success(f"User '{user_to_delete}' has been deleted.")
                st.rerun()
        else:
            st.warning("No users to delete.")

    st.write("---")
    st.subheader("🔑 Update User Password")
    
    u_update = st.selectbox("Select User to Update", users_df["username"].tolist(), key="update_user_select")
    new_pw_admin = st.text_input("New Password", type="password", key="new_pw_admin")
    
    if st.button("Update Password"):
        if new_pw_admin:
            conn = sqlite3.connect("aqi.db")
            cursor = conn.cursor()
            hashed_pw = bcrypt.hashpw(new_pw_admin.encode(), bcrypt.gensalt()).decode()
            cursor.execute("UPDATE users SET password=? WHERE username=?", (hashed_pw, u_update))
            conn.commit()
            conn.close()
            log_user_activity(st.session_state.user, f"Admin changed password for: {u_update}")
            st.success(f"Password for {u_update} updated successfully!")
        else:
            st.warning("Please enter a new password.")

    st.write("---")
    st.subheader("👮 Manage User Roles")
    
    col_role1, col_role2 = st.columns(2)
    with col_role1:
        u_role_select = st.selectbox("Select User", users_df["username"].tolist(), key="role_user")
    with col_role2:
        new_role_select = st.selectbox("Select New Role", ["user", "admin"], key="role_select")
        
    if st.button("Update Role"):
        conn = sqlite3.connect("aqi.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role=? WHERE username=?", (new_role_select, u_role_select))
        conn.commit()
        conn.close()
        log_user_activity(st.session_state.user, f"Changed role of {u_role_select} to {new_role_select}")
        st.success(f"Role for {u_role_select} updated to {new_role_select}.")
        st.rerun()

    with col2:
        st.subheader("⚠️ Reset Database")
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
    st.subheader("📜 Activity Logs")
    conn = sqlite3.connect("aqi.db")
    logs_df = pd.read_sql_query("SELECT * FROM activity_logs ORDER BY timestamp DESC", conn)
    conn.close()
    
    # Improved UI for Logs
    with st.container(height=400):
        for index, row in logs_df.iterrows():
            icon = "🔹"
            if "Login" in row['action']: icon = "🟢"
            elif "Logout" in row['action']: icon = "🚪"
            elif "Delete" in row['action'] or "RESET" in row['action']: icon = "🔴"
            elif "Update" in row['action'] or "Change" in row['action']: icon = "✏️"
            elif "Signup" in row['action']: icon = "✨"
            elif "Resolved" in row['action']: icon = "✅"
            
            st.markdown(f"**{icon} {row['timestamp']}** - `{row['username']}`: {row['action']}")

    csv = logs_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Logs as CSV",
        data=csv,
        file_name='activity_logs.csv',
        mime='text/csv',
    )

    st.write("---")
    st.subheader("📢 User Feedback Reports")
    conn = sqlite3.connect("aqi.db")
    feedback_df = pd.read_sql_query("SELECT * FROM feedback ORDER BY timestamp DESC", conn)
    conn.close()


    # Filter by Status
    status_filter = st.radio("Filter Status:", ["All", "Pending", "Resolved"], horizontal=True)
    if status_filter != "All":
        feedback_df = feedback_df[feedback_df["status"] == status_filter]

    st.dataframe(feedback_df, use_container_width=True)

    selected_feedback_id = st.selectbox("Select Feedback to View Details", feedback_df["id"].tolist(), key="selected_feedback")

    if selected_feedback_id:
        st.write("#### Feedback Details")
        selected_feedback = feedback_df[feedback_df["id"] == selected_feedback_id].iloc[0]

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
            st.write("#### 🛠️ Resolve Feedback")
            f_ids_to_resolve = st.multiselect("Select Feedback IDs to Resolve", pending_feedback["id"].tolist())
            
            if st.button("Mark Selected as Resolved"):
                if f_ids_to_resolve:
                    conn = sqlite3.connect("aqi.db")
                    cursor = conn.cursor()
                    placeholders = ', '.join('?' for _ in f_ids_to_resolve)
                    query = f"UPDATE feedback SET status='Resolved' WHERE id IN ({placeholders})"
                    cursor.execute(query, f_ids_to_resolve)
                    conn.commit()
                    conn.close()
                    log_user_activity(st.session_state.user, f"Bulk resolved feedback IDs: {f_ids_to_resolve}")
                    st.success(f"Feedback IDs {f_ids_to_resolve} marked as Resolved!")
                    st.rerun()
                else:
                    st.warning("Please select at least one feedback item.")
        elif not feedback_df.empty:
             st.info("All feedback items are resolved! 🎉")

# ==========================================================
# ---------------- FLOATING CHATBOT (BOTTOM RIGHT) ----------
# ==========================================================

st.markdown("""
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
""", unsafe_allow_html=True)


# Chat button toggle
if st.button("💬 Chat Assistant", key="open_chat"):
    st.session_state.chat_open = not st.session_state.chat_open


# Chat popup UI
if st.session_state.chat_open:

    st.markdown(f"""
    <div class="chat-popup">
        <div class="chat-title">🤖 AQI Assistant</div>
        <div class="chat-location">📍 {location_text}</div>
    """, unsafe_allow_html=True)

    # Chat History
    chat_html = '<div class="chat-body">'
    for chat in st.session_state.chat_history:
        if chat["role"] == "user":
            chat_html += f'<div class="user-msg">{chat["content"]}</div>'
        else:
            chat_html += f'<div class="bot-msg">{chat["content"]}</div>'
    chat_html += "</div>"

    st.markdown(chat_html, unsafe_allow_html=True)

    # Quick Buttons
    colq1, colq2, colq3 = st.columns(3)

    if colq1.button("🌫 AQI Today"):
        st.session_state.chat_history.append({"role": "user", "content": "What is the AQI today and is it safe?"})
        st.rerun()

    if colq2.button("😷 Mask?"):
        st.session_state.chat_history.append({"role": "user", "content": "Do I need to wear a mask today?"})
        st.rerun()

    if colq3.button("🏃 Exercise"):
        st.session_state.chat_history.append({"role": "user", "content": "Is it safe to do outdoor exercise today?"})
        st.rerun()

    # Input
    user_msg = st.text_input("Type your message", key="chat_input_msg")

    if st.button("Send", key="send_btn"):

        if user_msg.strip():
            st.session_state.chat_history.append({"role": "user", "content": user_msg})

            # Suggested city data
            city_data = df[df["City"] == suggested_city] if suggested_city else df
            avg_aqi = city_data["AQI"].mean()

            prompt = f"""
You are an Air Quality AI Assistant.

User location: {location_text}
Nearest City found in database: {suggested_city if suggested_city else "Not Found"}
Average AQI for that city: {avg_aqi}

User question: {user_msg}

Give:
1. Simple answer
2. Health advice
3. Suggestions like mask / indoor / outdoor
4. Warning if AQI is severe
"""

            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.6
                )

                ai_reply = response.choices[0].message.content

            except Exception as e:
                ai_reply = f"⚠️ OpenAI Error: {e}"

            st.session_state.chat_history.append({"role": "assistant", "content": ai_reply})
            st.rerun()

    if st.button("❌ Close Chat", key="close_chat"):
        st.session_state.chat_open = False
        st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)
