import streamlit as st
import pandas as pd
import mysql.connector
import plotly.express as px

# ------------------ MySQL Connection ------------------
def get_data():
    conn = mysql.connector.connect(
        host="localhost",
        user="root",
        password="Sure@2006",
        database="AQI"
    )

    query = "SELECT City, Date, AQI, PM25, PM10, NO2, SO2, CO, O3 FROM air_quality"
    df = pd.read_sql(query, conn)
    conn.close()

    df["Date"] = pd.to_datetime(df["Date"])
    return df


# ------------------ AQI Category Function ------------------
def aqi_category(aqi):
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


# ------------------ Streamlit Page Config ------------------
st.set_page_config(page_title="Advanced AQI Dashboard", layout="wide")
st.title("🌍 Advanced Air Quality Dashboard (MySQL + Streamlit)")

df = get_data()

# Add AQI Category Column
df["AQI_Category"] = df["AQI"].apply(aqi_category)

# ------------------ Sidebar Filters ------------------
st.sidebar.header("🔍 Filters")

city_list = sorted(df["City"].unique())
selected_cities = st.sidebar.multiselect("Select Cities", city_list, default=city_list[:3])

min_date = df["Date"].min()
max_date = df["Date"].max()

date_range = st.sidebar.date_input("Select Date Range", [min_date, max_date])

# Filter data
filtered_df = df[df["City"].isin(selected_cities)]

if len(date_range) == 2:
    start_date, end_date = date_range
    filtered_df = filtered_df[(filtered_df["Date"] >= pd.to_datetime(start_date)) &
                              (filtered_df["Date"] <= pd.to_datetime(end_date))]

st.write("### 📌 Selected Cities:", selected_cities)

# ------------------ KPI Metrics ------------------
col1, col2, col3, col4 = st.columns(4)

col1.metric("Total Records", len(filtered_df))
col2.metric("Average AQI", round(filtered_df["AQI"].mean(), 2))
col3.metric("Max AQI", round(filtered_df["AQI"].max(), 2))
col4.metric("Avg PM2.5", round(filtered_df["PM25"].mean(), 2))

# ------------------ Multi City AQI Trend ------------------
st.subheader("📈 Multi-City AQI Trend Over Time")

fig_line = px.line(
    filtered_df,
    x="Date",
    y="AQI",
    color="City",
    title="AQI Trend Comparison (Multiple Cities)"
)
st.plotly_chart(fig_line, use_container_width=True)

# ------------------ AQI Category Distribution ------------------
st.subheader("🟢 AQI Category Distribution")

fig_pie = px.pie(
    filtered_df,
    names="AQI_Category",
    title="AQI Category Share"
)
st.plotly_chart(fig_pie, use_container_width=True)

# ------------------ Average AQI by City Bar Chart ------------------
st.subheader("📊 Average AQI by City")

avg_city = filtered_df.groupby("City")["AQI"].mean().reset_index()

fig_bar = px.bar(
    avg_city,
    x="City",
    y="AQI",
    title="Average AQI City-wise",
    text_auto=True
)
st.plotly_chart(fig_bar, use_container_width=True)

# ------------------ Heatmap (Pollutants by City) ------------------
st.subheader("🔥 Heatmap: Average Pollutants by City")

pollutants = ["PM25", "PM10", "NO2", "SO2", "CO", "O3"]
heatmap_data = filtered_df.groupby("City")[pollutants].mean()

fig_heatmap = px.imshow(
    heatmap_data,
    text_auto=True,
    title="Pollutant Concentration Heatmap"
)
st.plotly_chart(fig_heatmap, use_container_width=True)

# ------------------ Map Visualization ------------------
st.subheader("🗺️ Map Visualization (City AQI)")

# City coordinates (India major cities)
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
    "UP": [26.8467, 80.9462]
}

map_df = filtered_df.groupby("City")["AQI"].mean().reset_index()

map_df["Latitude"] = map_df["City"].apply(lambda x: city_coordinates.get(x, [None, None])[0])
map_df["Longitude"] = map_df["City"].apply(lambda x: city_coordinates.get(x, [None, None])[1])

map_df = map_df.dropna()

fig_map = px.scatter_mapbox(
    map_df,
    lat="Latitude",
    lon="Longitude",
    size="AQI",
    hover_name="City",
    hover_data=["AQI"],
    zoom=4,
    title="Average AQI Map (Selected Cities)"
)

fig_map.update_layout(mapbox_style="open-street-map")
st.plotly_chart(fig_map, use_container_width=True)

# ------------------ Data Table ------------------
st.subheader("📋 Full Filtered Data")
st.dataframe(filtered_df)
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression

# Prepare dataset
X = df[["PM25", "PM10", "NO2", "SO2", "CO", "O3"]]
y = df["AQI"]

# Remove missing values
data = pd.concat([X, y], axis=1).dropna()
X = data[["PM25", "PM10", "NO2", "SO2", "CO", "O3"]]
y = data["AQI"]

# Train model
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
model = LinearRegression()
model.fit(X_train, y_train)

# Prediction UI
st.subheader("🤖 AQI Prediction")

pm25 = st.number_input("PM2.5", value=50.0)
pm10 = st.number_input("PM10", value=80.0)
no2 = st.number_input("NO2", value=20.0)
so2 = st.number_input("SO2", value=10.0)
co = st.number_input("CO", value=1.0)
o3 = st.number_input("O3", value=30.0)

if st.button("Predict AQI"):
    prediction = model.predict([[pm25, pm10, no2, so2, co, o3]])
    st.success(f"Predicted AQI: {round(prediction[0],2)}")
from fpdf import FPDF

st.subheader("📄 Export Report as PDF")

if st.button("Download PDF Report"):

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(200, 10, txt="Air Quality Dashboard Report", ln=True, align="C")
    pdf.ln(10)

    pdf.cell(200, 10, txt=f"Selected Cities: {', '.join(selected_cities)}", ln=True)
    pdf.cell(200, 10, txt=f"Total Records: {len(filtered_df)}", ln=True)
    pdf.cell(200, 10, txt=f"Average AQI: {round(filtered_df['AQI'].mean(),2)}", ln=True)
    pdf.cell(200, 10, txt=f"Max AQI: {round(filtered_df['AQI'].max(),2)}", ln=True)

    pdf.output("aqi_report.pdf")

    with open("aqi_report.pdf", "rb") as file:
        st.download_button("📥 Download PDF", file, file_name="aqi_report.pdf")

st.sidebar.title("🔐 Login System")

users = {
    "admin": "suresh",
    "suresh": "suresh123"
}

username = st.sidebar.text_input("Username")
password = st.sidebar.text_input("Password", type="password")

if st.sidebar.button("Login"):
    if username in users and users[username] == password:
        st.session_state["logged_in"] = True
        st.session_state["user"] = username
        st.success("Login Successful ✅")
    else:
        st.error("Invalid Username or Password ❌")

if "logged_in" not in st.session_state or st.session_state["logged_in"] == False:
    st.warning("Please Login to access dashboard")
    st.stop()
