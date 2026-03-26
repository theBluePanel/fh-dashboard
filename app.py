import streamlit as st
import pandas as pd
from plotly.subplots import make_subplots
import plotly.graph_objects as go
import streamlit_authenticator as stauth

st.set_page_config(page_title="Datacake Dashboard", layout="wide")

# ---- AUTH ----
# Convert secrets to plain dict so streamlit-authenticator can write to it
credentials = {
    "usernames": {
        username: dict(user_data)
        for username, user_data in st.secrets["auth"]["credentials"]["usernames"].items()
    }
}

authenticator = stauth.Authenticate(
    credentials,
    st.secrets["auth"]["cookie_name"],
    st.secrets["auth"]["cookie_key"],
    st.secrets["auth"]["cookie_expiry_days"],
    auto_hash=True
)

authenticator.login(location='main')

if st.session_state.get("authentication_status") is False:
    st.error("Incorrect username or password")
    st.stop()
elif st.session_state.get("authentication_status") is None:
    st.title("FH Dashboard")
    st.stop()

# ---- DATA ----
# Load sensor configuration from secrets
username = st.session_state.get("username", "")
allowed_urls = list(st.secrets["auth"]["credentials"]["usernames"].get(username, {}).get("allowed_urls", []))
all_sensors_config = {
    name: dict(cfg) for name, cfg in st.secrets["sensors"].items()
    if cfg.get("data_url") in allowed_urls
}

@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_data(data_url_key):
    SHEET_CSV_URL = st.secrets[data_url_key]
    try:
        # Ensure URL starts with http/https
        if not SHEET_CSV_URL.startswith(('http://', 'https://')):
            st.error(f"Invalid URL format: {SHEET_CSV_URL}")
            st.stop()
        return pd.read_csv(SHEET_CSV_URL)
    except Exception as e:
        st.error(f"Failed to load data from: {SHEET_CSV_URL}")
        st.error(f"Error: {str(e)}")
        raise

# Initialize session state for selected sensor
if 'selected_sensor' not in st.session_state:
    st.session_state.selected_sensor = list(all_sensors_config.keys())[0]

# Sidebar sensor selector at the top
st.sidebar.header("Sensor Selection")
selected_sensor = st.sidebar.selectbox(
    "Select Sensor",
    options=list(all_sensors_config.keys()),
    index=list(all_sensors_config.keys()).index(st.session_state.selected_sensor)
)

# Update selected sensor in session state
if selected_sensor != st.session_state.selected_sensor:
    st.session_state.selected_sensor = selected_sensor

# Get current sensor config
sensor_config = all_sensors_config[st.session_state.selected_sensor]

# Load data for selected sensor
df = load_data(sensor_config['data_url'])

# Convert datetime to pandas datetime
df['datetime'] = pd.to_datetime(df['datetime'])

# Initialize session state for date range
if 'start_date' not in st.session_state:
    st.session_state.start_date = (pd.Timestamp.today() - pd.Timedelta(days=7)).date()
if 'end_date' not in st.session_state:
    st.session_state.end_date = pd.Timestamp.today().date()

# Sidebar controls
st.sidebar.header("Settings")

# Date range selector - compact layout
col1, col2 = st.sidebar.columns([1, 2])
with col1:
    st.write("Start Date:")
with col2:
    start_date_input = st.date_input("Start Date", value=st.session_state.start_date, label_visibility="collapsed")

col1, col2 = st.sidebar.columns([1, 2])
with col1:
    st.write("End Date:")
with col2:
    end_date_input = st.date_input("End Date", value=st.session_state.end_date, label_visibility="collapsed")

# Sampling period selector - compact layout
col1, col2 = st.sidebar.columns([1, 2])
with col1:
    st.write("Sampling:")
with col2:
    sampling_period = st.selectbox(
        "Sampling Period",
        options=["30min", "1H", "1D", "1M"],
        format_func=lambda x: {"30min": "30 min", "1H": "1 hour", "1D": "1 day", "1M": "1 month"}[x],
        label_visibility="collapsed"
    )

# Check if dates have changed
dates_changed = (start_date_input != st.session_state.start_date or 
                 end_date_input != st.session_state.end_date)

# Update Data button with visual indicator
button_label = "⚠️ Update Data" if dates_changed else "Update Data"
if st.sidebar.button(button_label, use_container_width=True):
    st.session_state.start_date = start_date_input
    st.session_state.end_date = end_date_input
    st.cache_data.clear()  # Clear cache to refresh data
    st.rerun()

# Filter data by date range using session state values
df_filtered = df[(df['datetime'].dt.date >= st.session_state.start_date) & 
                 (df['datetime'].dt.date <= st.session_state.end_date)]

# Show statistics in sidebar
st.sidebar.markdown("---")
st.sidebar.subheader("Period Statistics")

# Calculate total variation for COUNT_TIME columns
count_cols = [col for col in df_filtered.columns if col.startswith('COUNT_TIME')]
for col in count_cols:
    valid_data = df_filtered[col].dropna()
    if len(valid_data) > 0:
        total_variation = valid_data.iloc[-1] - valid_data.iloc[0]
        # Get friendly name and unit from config
        friendly_name = sensor_config.get(col, col)
        unit = sensor_config.get(f"{col}_UNIT", "")
        label = friendly_name if friendly_name else col
        value_str = f"{int(total_variation):,} {unit}" if unit else f"{int(total_variation):,}"
        st.sidebar.metric(label=label, value=value_str)

# Debug: show battery stats
if 'BATTERY' in df_filtered.columns:
    battery_vals = df_filtered['BATTERY'].dropna()
    if len(battery_vals) > 0:
        st.sidebar.write(f"Battery: {battery_vals.min():.2f}V - {battery_vals.max():.2f}V")

# Resample data
df_resampled = df_filtered.set_index('datetime').resample(sampling_period).last().reset_index()

# Calculate differences (consumption) for COUNT_TIME columns
count_cols = [col for col in df_filtered.columns if col.startswith('COUNT_TIME')]
df_diff = df_resampled.copy()
for col in count_cols:
    df_diff[f'{col}_diff'] = df_resampled[col].diff().fillna(0)

st.title(f"📈 {st.session_state.selected_sensor} Dashboard")

# Create dual-axis chart using subplots
fig = make_subplots(specs=[[{"secondary_y": True}]])

# Add bar traces for consumption (differences) for COUNT_TIME columns
for col in count_cols:
    friendly_name = sensor_config.get(col, col)
    unit = sensor_config.get(f"{col}_UNIT", "")
    label = f"{friendly_name} consumption" if friendly_name else f"{col} consumption"
    if unit:
        label += f" ({unit})"
    fig.add_trace(
        go.Bar(x=df_diff['datetime'], y=df_diff[f'{col}_diff'], name=label),
        secondary_y=False
    )

# Add line traces for cumulative COUNT_TIME columns
for col in count_cols:
    friendly_name = sensor_config.get(col, col)
    label = friendly_name if friendly_name else col
    fig.add_trace(
        go.Scatter(x=df_resampled['datetime'], y=df_resampled[col], name=label, mode='lines', visible='legendonly'),
        secondary_y=False
    )

# Add BATTERY on secondary y-axis (filter out NaN values)
if 'BATTERY' in df_resampled.columns:
    battery_data = df_resampled[['datetime', 'BATTERY']].dropna()
    fig.add_trace(
        go.Scatter(
            x=battery_data['datetime'], 
            y=battery_data['BATTERY'], 
            name='BATTERY', 
            mode='lines+markers',
            line=dict(color='green', width=2),
            marker=dict(size=4)
        ),
        secondary_y=True
    )

# Update axes
fig.update_xaxes(
    title_text="Time",
    range=[pd.Timestamp(st.session_state.start_date), pd.Timestamp(st.session_state.end_date) + pd.Timedelta(days=1)]
)
fig.update_yaxes(title_text="Consumption / Count", secondary_y=False)
fig.update_yaxes(title_text="Battery (V)", secondary_y=True, showgrid=False, range=[2.8, 4.2])

fig.update_layout(
    hovermode='x unified',
    height=600,
    legend=dict(
        orientation="h",
        yanchor="bottom",
        y=1.02,
        xanchor="left",
        x=0
    ),
    barmode='group'
)

st.plotly_chart(fig, use_container_width=True)

# Logout at the bottom of the sidebar
st.sidebar.markdown("---")
st.sidebar.write(f"Logged in as: {st.session_state.get('name', '')}")
authenticator.logout(location='sidebar')
