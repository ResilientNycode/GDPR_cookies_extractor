import logging
import sys
import os
from datetime import datetime

def setup_logging():
    """
    Configures the logger to write to both a timestamped log file and the console.
    """
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = os.path.join(output_dir, f"gdpr_analysis_{timestamp}.log")

    log_format = '%(asctime)s - %(levelname)s - %(message)s'

    logging.basicConfig(
        level=logging.DEBUG,  
        format=log_format,
        handlers=[
            logging.FileHandler(log_filename),
            logging.StreamHandler(sys.stdout)
        ]
    )

    logger = logging.getLogger(__name__)

    logger.info("Logging configured. Writing to console and log file: %s", log_filename)