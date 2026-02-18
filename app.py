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


# ---------------- USER DATABASE (Signup/Login) ----------------
def init_user_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)
    conn.commit()
    conn.close()


def signup_user(username, password):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    try:
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, hashed_pw))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False


def login_user(username, password):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    cursor.execute("SELECT password FROM users WHERE username=?", (username,))
    row = cursor.fetchone()
    conn.close()

    if row:
        stored_hash = row[0].encode()
        return bcrypt.checkpw(password.encode(), stored_hash)

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

    st.markdown("<h1 style='text-align:center;'>🌍 AQI Dashboard System</h1>", unsafe_allow_html=True)
    st.markdown("<h4 style='text-align:center;'>Signup / Login Portal</h4>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["🔑 Login", "📝 Signup"])

    with tab1:
        st.subheader("Login Account")
        username = st.text_input("Username", key="login_user")
        password = st.text_input("Password", type="password", key="login_pass")

        if st.button("Login"):
            if login_user(username, password):
                st.session_state.logged_in = True
                st.session_state.user = username
                st.success("Login Successful ✅")
                st.rerun()
            else:
                st.error("Invalid username or password ❌")

    with tab2:
        st.subheader("Create New Account")
        new_username = st.text_input("New Username", key="signup_user")
        new_password = st.text_input("New Password", type="password", key="signup_pass")

        if st.button("Signup"):
            if signup_user(new_username, new_password):
                st.success("Account Created Successfully ✅ Please Login Now")
            else:
                st.error("Username already exists ❌")

    st.stop()


# ---------------- SIDEBAR MENU ----------------
st.sidebar.title("🌍 AQI Dashboard Menu")
menu = st.sidebar.radio("Navigation", ["Dashboard", "Prediction", "Report Download", "Raw Data"])
st.sidebar.success(f"Logged in as: {st.session_state.user}")

if st.sidebar.button("🚪 Logout"):
    st.session_state.logged_in = False
    st.session_state.chat_history = []
    st.session_state.chat_open = False
    st.rerun()


# ---------------- FILTERS ----------------
st.sidebar.header("🔍 Filters")

city_list = sorted(df["City"].dropna().unique())
selected_cities = st.sidebar.multiselect("Select Cities", city_list, default=city_list[:3])

min_date = df["Date"].min()
max_date = df["Date"].max()

date_range = st.sidebar.date_input("Select Date Range", [min_date, max_date])

filtered_df = df[df["City"].isin(selected_cities)]

if len(date_range) == 2:
    start_date, end_date = date_range
    filtered_df = filtered_df[(filtered_df["Date"] >= pd.to_datetime(start_date)) &
                              (filtered_df["Date"] <= pd.to_datetime(end_date))]

# ---------------- FILTERS ----------------
st.sidebar.header("🔍 Filters")

city_list = sorted(df["City"].dropna().unique())

# ✅ Add this here
st.sidebar.subheader("📍 Select Location")
manual_city = st.sidebar.selectbox("Choose your City", city_list)

suggested_city = manual_city
location_text = manual_city

# Existing filter
selected_cities = st.sidebar.multiselect("Select Cities", city_list, default=city_list[:3])

# ---------------- LOCATION SUGGESTION ----------------
city, region, country = get_user_location()
location_text = f"{city}, {region}, {country}"

suggested_city = None
if city in df["City"].values:
    suggested_city = city
    st.sidebar.info(f"📍 Location Detected: {suggested_city}")
else:
    st.sidebar.warning("📍 Location city not found in AQI database")


# ---------------- DASHBOARD PAGE ----------------
if menu == "Dashboard":

    st.title("📊 Advanced Air Quality Dashboard")
    st.write("### 📌 Selected Cities:", ", ".join(selected_cities))

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


# ---------------- PREDICTION PAGE ----------------
elif menu == "Prediction":

    st.title("🤖 AQI Prediction (Machine Learning)")
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
