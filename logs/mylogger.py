import logging
import os

# Global logger instance imported by other modules.
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.propagate = False

# Add the console handler only once.
if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(console_handler)

# The main program calls this function to add a file handler dynamically.
def setup_logger(log_dir, trial):
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"trial_{trial}.log")

    # Avoid adding the same file handler more than once.
    if not any(isinstance(h, logging.FileHandler) and h.baseFilename == os.path.abspath(log_file) for h in logger.handlers):
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(file_handler)

    return logger  # Return it for optional continued use in main.py.
