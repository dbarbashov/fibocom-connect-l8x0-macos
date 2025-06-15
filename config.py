# Application Configuration

# NCM interface MAC address (use 'XX-XX-XX-XX-XX-XX' format)
# This needs to be discoverable or configurable for different OS
MODEM_MAC_ADDRESS = "00-00-11-12-13-14"

# Serial port identification:
# On Linux, this could be part of the device path, e.g., "ttyACM" or "ttyUSB"
# On macOS, e.g., "cu.usbmodem"
# The script will try to find a port matching this pattern.
# PowerShell's "*acm2*" is specific. We'll need a robust discovery method.
SERIAL_PORT_KEYWORD = "acm" # Example, adjust as needed

APN = "internet"
APN_USER = ""
APN_PASS = ""

# Override DNS settings. Example: ['8.8.8.8', '1.1.1.1']
DNS_OVERRIDE = []

# Timeout for AT commands in seconds
AT_COMMAND_TIMEOUT = 10 # Increased from 1s default in PS for some commands
AT_COMMAND_LONG_TIMEOUT = 60 # For commands like COPS=0,0

# Application version
APP_VERSION = "Fibocom Connect vYYYY.MM.DD (Python Port)"

# Logging configuration
LOG_LEVEL = "DEBUG" # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_DIR = "logs" # Main application logs
LOG_FILE_BASENAME = "app-log" # Log files will be named app-log-YYYYMMDDHHMMSS.txt

# AT Command Logging Configuration
AT_LOG_DIR = "at_command_logs"
AT_LOG_FILENAME_FORMAT = "%Y-%m-%dT%H%M%SZ.log" # UTC Timestamp format
AT_LOGGER_NAME = "ATCommands"
