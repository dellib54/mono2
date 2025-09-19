import streamlit as st
import pandas as pd
import io
from datetime import datetime, timedelta, date
from snowflake.snowpark.context import get_active_session
from snowflake.snowpark.functions import col, lit, call_function

# @st.fragment
@st.cache_data
def convert_for_download(df):
    return df.to_csv(index=False).encode("utf-8")

st.set_page_config(page_title="Temperature & Humidity Trends", layout="wide")
st.title("🌡️ Temperature & 💧 Humidity Trends")

session = get_active_session()
base_df = session.table("telemetry_curated")

# --- sidebar filters
with st.sidebar:
    st.header("Filters")

    # date range bounds (from data)
    bounds = session.sql("""
        SELECT
          MIN(ts) AS min_ts,
          MAX(ts) AS max_ts
        FROM telemetry_curated
    """).to_pandas()

    min_ts = pd.to_datetime(bounds["MIN_TS"][0]) if not bounds.empty else pd.Timestamp.utcnow() - pd.Timedelta(days=1)
    max_ts = pd.to_datetime(bounds["MAX_TS"][0]) if not bounds.empty else pd.Timestamp.utcnow()

    default_start = max_ts - pd.Timedelta(hours=1)
    start_date, end_date = st.date_input(
        "Date range",
        (default_start.date(), max_ts.date()),
        min_value=min_ts.date(),
        max_value=max_ts.date()
    )

    # time-of-day refinement (optional)
    col_time1, col_time2 = st.columns(2)
    with col_time1:
        start_time = st.time_input("Start time", value=default_start.to_pydatetime().time())
    with col_time2:
        end_time = st.time_input("End time", value=max_ts.to_pydatetime().time())

    # granularity choice
    granularity = st.selectbox(
        "Time granularity",
        options=["minute", "hour", "day"],
        index=0
    )

    # site/room filters
    sites = session.sql("SELECT DISTINCT site FROM telemetry_curated ORDER BY site").to_pandas()["SITE"].dropna().tolist()
    sel_sites = st.multiselect("Site", options=sites, default=sites)

    rooms = session.sql("SELECT DISTINCT room FROM telemetry_curated ORDER BY room").to_pandas()["ROOM"].dropna().tolist()
    sel_rooms = st.multiselect("Room", options=rooms, default=rooms)

# compose timestamp range
start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

# --- filter dataframe with Snowpark
df = base_df.filter((col("ts") >= lit(start_dt)) & (col("ts") <= lit(end_dt)))

if sel_sites:
    df = df.filter(col("site").isin([lit(s) for s in sel_sites]))
if sel_rooms:
    df = df.filter(col("room").isin([lit(r) for r in sel_rooms]))

# --- bucket time
# DATE_TRUNC('minute'|'hour'|'day', ts)
ts_bucket = call_function("DATE_TRUNC", lit(granularity), col("ts"))
df_buck = (
    df.select(
        ts_bucket.alias("ts_bucket"),
        col("site"), col("room"), col("sensor_id"),
        col("temp_c"), col("humidity")
    )
    .group_by("ts_bucket", "site", "room")
    .agg(
        call_function("AVG", col("temp_c")).alias("avg_temp_c"),
        call_function("AVG", col("humidity")).alias("avg_humidity")
    )
)

pdf = df_buck.sort("ts_bucket").to_pandas()

if pdf.empty:
    st.info("No data found for the selected filters.")
    st.stop()

# --- KPIs
kcol1, kcol2, kcol3, kcol4 = st.columns(4)
kcol1.metric("Avg Temp (°C)", f"{pdf['AVG_TEMP_C'].mean():.2f}")
kcol2.metric("Avg Humidity (%)", f"{pdf['AVG_HUMIDITY'].mean():.1f}")
kcol3.metric("Max Temp (°C)", f"{pdf['AVG_TEMP_C'].max():.2f}")
kcol4.metric("Min Temp (°C)", f"{pdf['AVG_TEMP_C'].min():.2f}")

# --- per-location selection for plotting
# Build a "location" label for convenience
pdf["location"] = pdf["SITE"].astype(str) + " / " + pdf["ROOM"].astype(str)

locations = sorted(pdf["location"].unique())
sel_locs = st.multiselect("Locations to plot", options=locations, default=locations[: min(5, len(locations))])

plot_df = pdf[pdf["location"].isin(sel_locs)].copy()

# --- charts (temperature & humidity)
st.subheader("Temperature Trend (by selected locations)")
if not plot_df.empty:
    temp_wide = plot_df.pivot(index="TS_BUCKET", columns="location", values="AVG_TEMP_C").sort_index()
    st.line_chart(temp_wide)

st.subheader("Humidity Trend (by selected locations)")
if not plot_df.empty:
    hum_wide = plot_df.pivot(index="TS_BUCKET", columns="location", values="AVG_HUMIDITY").sort_index()
    st.line_chart(hum_wide)

st.download_button(
    label="Download CSV",
    data=convert_for_download(plot_df),
    file_name="data_aggregated.csv",
    mime="text/csv",
    # icon=":material/download:",
)

st.button("A")

# --- detailed table (optional)
with st.expander("Show aggregated data table"):
    st.dataframe(plot_df.sort_values("TS_BUCKET"), use_container_width=True)
