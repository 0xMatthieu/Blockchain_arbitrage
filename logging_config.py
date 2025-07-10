import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging():
    """Sets up logging for the application."""
    log_directory = "logs"
    if not os.path.exists(log_directory):
        os.makedirs(log_directory)

    log_file = os.path.join(log_directory, "arbitrage_bot.log")

    # Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Prevent adding handlers multiple times if this function is called more than once
    if logger.hasHandlers():
        logger.handlers.clear()

    # --- File Handler ---
    # Rotates logs, keeping 5 files of 5MB each.
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # --- Console Handler ---
    console_handler = logging.StreamHandler()
    # Use a simpler format for console output, similar to print()
    console_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
