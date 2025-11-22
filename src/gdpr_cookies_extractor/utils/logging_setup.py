import logging
import sys
import os
from datetime import datetime
import json

class ContextFilter(logging.Filter):
    """
    A logging filter that injects contextual information (site, scenario) into log records.
    """
    def __init__(self):
        super().__init__()
        self.site = 'general'
        self.scenario = ''

    def filter(self, record):
        record.site = self.site
        record.scenario = self.scenario
        return True

# Module-level instance of the filter
context_filter = ContextFilter()

def set_log_context(site: str, scenario: str):
    """Sets the global logging context."""
    context_filter.site = site
    context_filter.scenario = scenario

def clear_log_context():
    """Clears the global logging context."""
    context_filter.site = 'general'
    context_filter.scenario = ''

def setup_logging():
    """
    Configures the logger to write to both a timestamped log file and the console.
    """
    output_dir = "logs"
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = os.path.join(output_dir, f"gdpr_analysis_{timestamp}.log")

    log_format = '%(asctime)s - %(levelname)s - [%(site)s - %(scenario)s] - %(message)s'

    # Load log level from config.json or default to INFO
    log_level_str = "DEBUG"
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        log_level_str = config.get('logging', {}).get('level', 'DEBUG').upper()
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass # Use default INFO

    log_level = getattr(logging, log_level_str, logging.INFO)

    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove any existing handlers
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(log_format)

    # Create and configure handlers
    file_handler = logging.FileHandler(log_filename)
    file_handler.addFilter(context_filter)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.addFilter(context_filter)
    stream_handler.setFormatter(formatter)

    # Add handlers to the root logger
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)

    logger = logging.getLogger(__name__)
    logger.info("Logging configured. Writing to console and log file: %s", log_filename)