import streamlit as st
import threading
import time
import io
import logging
from collections import deque
from contextlib import redirect_stdout, redirect_stderr

from main import ArbitrageBot, setup_logging

# --- Streamlit App Configuration ---
st.set_page_config(
    page_title="DEX Arbitrage Bot",
    page_icon="🤖",
    layout="wide",
)

# --- Application State ---
if 'bot_started' not in st.session_state:
    st.session_state.bot_started = False
    st.session_state.bot_thread = None
    st.session_state.spread_info = {}
    st.session_state.log_stream = io.StringIO()

def bot_target_with_logging(bot, log_stream):
    """Wraps the bot's run method to capture stdout/stderr."""
    with redirect_stdout(log_stream), redirect_stderr(log_stream):
        # The bot will set up its own logging; we just capture the output.
        setup_logging() 
        try:
            bot.run()
        except Exception:
            logging.error("Bot thread crashed.", exc_info=True)


def start_bot():
    if not st.session_state.bot_started:
        st.session_state.bot_started = True
        
        bot = ArbitrageBot(st.session_state.spread_info)
        
        st.session_state.bot_thread = threading.Thread(
            target=bot_target_with_logging,
            args=(bot, st.session_state.log_stream),
            daemon=True
        )
        st.session_state.bot_thread.start()
        st.toast("Bot started!", icon="🚀")
        st.experimental_rerun()

# --- UI Layout ---
st.title("🤖 DEX Arbitrage Bot Dashboard")

st.sidebar.button("Start Bot", on_click=start_bot, disabled=st.session_state.bot_started, use_container_width=True)

if not st.session_state.bot_started:
    st.info("Bot is not running. Click 'Start Bot' in the sidebar to begin.")
    st.stop()

# --- Main Dashboard Area ---
col1, col2 = st.columns([2, 1.5])

with col1:
    st.header("📈 Best Spread Opportunities")
    spread_placeholder = st.empty()

with col2:
    st.header("📋 Live Log / Console")
    log_placeholder = st.empty()

# --- Display Loop ---
while True:
    # --- Update spread info ---
    with spread_placeholder.container():
        if st.session_state.spread_info:
            # Sort for consistent display order
            sorted_spreads = sorted(st.session_state.spread_info.items(), key=lambda item: str(item[1]))
            for _, info_line in sorted_spreads:
                st.text(info_line)
        else:
            st.info("Waiting for spread data from the bot...")
    
    # --- Update log display ---
    with log_placeholder.container():
        log_contents = st.session_state.log_stream.getvalue()
        st.text_area("Logs", value=log_contents, height=400, disabled=True)

    time.sleep(10)
    st.experimental_rerun()
