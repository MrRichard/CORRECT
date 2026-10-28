import logging
import os
import sys

# Define a specific logger for transaction events
TRANSACTION_LOGGER_NAME = "transaction"

def setup_logging(config):
    """Sets up logging for the application based on the provided configuration."""
    log_config = config.get("logging", {})
    log_level_str = log_config.get("level", "INFO").upper()
    log_format_str = log_config.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    
    log_level = getattr(logging, log_level_str, logging.INFO)
    
    logs_dir = os.path.expanduser(config.get("directories", {}).get("logs", "~/CORRECT_logs"))
    app_log_filename = log_config.get("application_log_file", "application.log")
    trans_log_filename = log_config.get("transaction_log_file", "transaction.log")

    app_log_path = os.path.join(logs_dir, app_log_filename)
    trans_log_path = os.path.join(logs_dir, trans_log_filename)

    try:
        os.makedirs(logs_dir, exist_ok=True)
    except OSError as e:
        # Fallback to console logging if directory creation fails
        logging.basicConfig(level=log_level, format=log_format_str, stream=sys.stdout)
        logging.error(f"Could not create logs directory {logs_dir}: {e}. Falling back to console logging.")
        return

    logging.basicConfig(level=log_level, format=log_format_str, handlers=[logging.StreamHandler(sys.stdout)])

    # Route warnings.warn() output into the logging system so library warnings
    # (e.g. rt-utils' RTSTRUCT series reference check) land in application.log
    # instead of only appearing on stderr.
    logging.captureWarnings(True)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level) # Ensure root logger level is set

    # Application File Handler
    try:
        app_file_handler = logging.FileHandler(app_log_path)
        app_file_handler.setFormatter(logging.Formatter(log_format_str))
        app_file_handler.setLevel(log_level)
        root_logger.addHandler(app_file_handler)
        logging.info(f"Application logging configured. Level: {log_level_str}. File: {app_log_path}")
    except Exception as e:
        logging.error(f"Failed to configure application file logger at {app_log_path}: {e}", exc_info=True)

    # Transaction File Handler (for a specific logger)
    try:
        transaction_logger = logging.getLogger(TRANSACTION_LOGGER_NAME)
        transaction_logger.setLevel(log_level) # Transactions should also respect the global level or have their own
        transaction_logger.propagate = False # Do not propagate to root logger to avoid duplicate file/console logs

        trans_file_handler = logging.FileHandler(trans_log_path)
        # Transaction log might have a simpler format, or a specific one
        trans_log_format_str = log_config.get("transaction_log_format", "%(asctime)s [%(levelname)s]: %(message)s")
        trans_file_handler.setFormatter(logging.Formatter(trans_log_format_str))
        trans_file_handler.setLevel(log_level)
        transaction_logger.addHandler(trans_file_handler)
        transaction_logger.info(f"Transaction logging configured. Level: {log_level_str}. File: {trans_log_path}") # Log to its own file
    except Exception as e:
        logging.error(f"Failed to configure transaction file logger at {trans_log_path}: {e}", exc_info=True)
