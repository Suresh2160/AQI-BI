import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from sklearn.linear_model import LinearRegression
from fpdf import FPDF
import numpy as np

# ------------------- PAGE CONFIG -------------------
st.set_page_config(page_title="AQI Dashboard", page_icon="🌍", layout="wide")

# ------------------- LOGIN SYSTEM -------------------
users = {
    "admin": "suresh",
    "suresh": "suresh123"
}

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if st.session_state.logged_in == False:
    st.title("🔐 Login - AQI Dashboard")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if username in users and users[username] == password:
            st.session_state.logged_in = True
            st.session_state.user = username
            st.success("Login Successful ✅")
            st.rerun()
        else:
            st.error("Invalid Username or Password ❌")

    st.stop()

# ------------------- MYSQL CONNECTION -------------------
def get_data():
    conn = sqlite3.connect("aqi.db")   # database file name

    query = "SELECT City, Date, AQI, PM25, PM10, NO2, SO2, CO, O3 FROM air_quality"
    df = pd.read_sql_query(query, conn)

    conn.close()

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.replace({np.nan: None})
    return df
df = get_data() 


# ------------------- AQI CATEGORY -------------------
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

# ------------------- SIDEBAR NAVIGATION -------------------
st.sidebar.title("🌍 AQI Dashboard Menu")
menu = st.sidebar.radio("Navigation", ["Dashboard", "Prediction", "Report Download", "Raw Data"])

st.sidebar.write("---")
st.sidebar.success(f"Logged in as: {st.session_state.user}")

# ------------------- FILTERS -------------------
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

# ------------------- DASHBOARD PAGE -------------------
if menu == "Dashboard":

    st.title("📊 Advanced Air Quality Dashboard")

    st.write("### 📌 Selected Cities:", ", ".join(selected_cities))

    # KPIs
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Total Records", len(filtered_df))
    col2.metric("Average AQI", round(filtered_df["AQI"].mean(), 2))
    col3.metric("Max AQI", round(filtered_df["AQI"].max(), 2))
    col4.metric("Average PM2.5", round(filtered_df["PM25"].mean(), 2))

    st.write("---")

    # AQI Trend
    st.subheader("📈 AQI Trend (Multi City Comparison)")
    fig_line = px.line(filtered_df, x="Date", y="AQI", color="City", markers=True)
    st.plotly_chart(fig_line, use_container_width=True)

    # AQI Category Pie + Avg AQI Bar
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

    # Heatmap
    st.subheader("🔥 Pollutant Heatmap (City vs Pollutants)")
    pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
    heatmap_data = filtered_df.groupby("City")[pollutants].mean()

    fig_heat = px.imshow(heatmap_data, text_auto=True)
    st.plotly_chart(fig_heat, use_container_width=True)

    st.write("---")

    # Map Visualization
    st.subheader("🗺️ AQI City Map View")

    city_coordinates = {
        "Delhi": [28.6139, 77.2090],
        "Mumbai": [19.0760, 72.8777],
        "Chennai": [13.0827, 80.2707],
        "Bangalore": [12.9716, 77.5946],
        "Hyderabad": [17.3850, 78.4867],
        "Kolkata": [22.5726, 88.3639],
        "Pune": [18.5204, 73.8567],
        "Lucknow": [26.8467, 80.9462],
        "Ahmedabad": [23.0225, 72.5714],
        "Ahamadabad": [23.0225, 72.5714],
        "UP": [26.8467, 80.9462]
    }

    map_df = filtered_df.groupby("City")["AQI"].mean().reset_index()
    map_df["Latitude"] = map_df["City"].apply(lambda x: city_coordinates.get(x, [None, None])[0])
    map_df["Longitude"] = map_df["City"].apply(lambda x: city_coordinates.get(x, [None, None])[1])
    map_df = map_df.dropna()

    map_df["AQI"] = pd.to_numeric(map_df["AQI"], errors="coerce")
    map_df = map_df.dropna(subset=["AQI"])

    fig_map = px.scatter_mapbox(
        map_df,
        lat="Latitude",
        lon="Longitude",
        size="AQI",
        hover_name="City",
        hover_data=["AQI"],
        zoom=4
    )

    fig_map.update_layout(mapbox_style="open-street-map")
    st.plotly_chart(fig_map, use_container_width=True)

# ------------------- PREDICTION PAGE -------------------
elif menu == "Prediction":

    st.title("🤖 AQI Prediction (Machine Learning)")

    st.write("This model predicts AQI based on pollutant levels.")

    # Prepare training data
    train_df = df[["PM25", "PM10", "NO2", "SO2", "CO", "O3", "AQI"]].dropna()

    X = train_df[["PM25", "PM10", "NO2", "SO2", "CO", "O3"]]
    y = train_df["AQI"]

    model = LinearRegression()
    model.fit(X, y)

    st.subheader("Enter Pollutant Values")

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

# ------------------- REPORT DOWNLOAD PAGE -------------------
elif menu == "Report Download":

    st.title("📄 Download AQI Report (PDF)")

    st.write("This will generate a PDF report based on selected cities & date range.")

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
        pdf.ln(10)

        pdf.cell(200, 10, txt="Pollutant Averages:", ln=True)
        pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]

        for p in pollutants:
            pdf.cell(200, 10, txt=f"{p} Avg: {round(filtered_df[p].mean(),2)}", ln=True)

        pdf.output("aqi_report.pdf")

        with open("aqi_report.pdf", "rb") as file:
            st.download_button("📥 Download PDF", file, file_name="aqi_report.pdf")

# ------------------- RAW DATA PAGE -------------------
elif menu == "Raw Data":

    st.title("📋 Raw Data Viewer")

    st.dataframe(filtered_df)
