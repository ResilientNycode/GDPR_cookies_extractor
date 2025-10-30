import logging
import sys
import os
from datetime import datetime
import json

def setup_logging():
    """
    Configures the logger to write to both a timestamped log file and the console.
    """
    output_dir = "logs"
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = os.path.join(output_dir, f"gdpr_analysis_{timestamp}.log")

    log_format = '%(asctime)s - %(levelname)s - %(message)s'

    # Load log level from config.json or default to INFO
    log_level_str = "DEBUG"
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        log_level_str = config.get('logging', {}).get('level', 'DEBUG').upper()
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass # Use default INFO

    log_level = getattr(logging, log_level_str, logging.INFO)

    logging.basicConfig(
        level=log_level,  
        format=log_format,
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger = logging.getLogger(__name__)

    logger.info("Logging configured. Writing to console and log file: %s", log_filename)