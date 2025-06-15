# Fibocom Connect (Python Port)

This project is a Python port of the original PowerShell-based [fibocom-connect-l8x0](https://github.com/prusa-dev/fibocom-connect-l8x0) utility by Prusa-Dev. It provides a cross-platform solution (tested on macOS and Linux) for managing and connecting to the internet using Fibocom L8x0 series cellular modems.

## Features

*   **Cross-platform:** Designed to run on Linux and macOS.
*   **Automatic Discovery:** Automatically detects the modem's serial control port and network interface (via its MAC address).
*   **Connection Management:** Initializes the modem and establishes a data session using a proven sequence of AT commands.
*   **Network Configuration:** Configures the host operating system's network interface with the IP, subnet, gateway, and DNS information provided by the carrier.
*   **Real-time Monitoring:** Displays a live status view in the terminal, showing signal strength (RSSI, RSRP, RSRQ, SINR), operator, connection mode, band information, and more.
*   **Connection Watchdog:** Monitors the connection and automatically attempts to reconnect if the serial port or network connectivity is lost.
*   **Detailed Logging:** Creates separate, timestamped logs for both the main application flow and the raw AT command communication, which is invaluable for debugging.

## Prerequisites

*   Python 3.6+
*   A Fibocom L8x0 series modem (e.g., L850-GL) connected to your computer.
*   Administrator (`sudo`/root) privileges are required to configure the network interface.

## Installation and Configuration

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd <repository-directory>
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure the script:**
    Open `src/config.py` in a text editor and modify the settings to match your hardware and carrier.

    **Required settings:**
    *   `MODEM_MAC_ADDRESS`: Set this to the MAC address of your modem's network interface.
        *   On Linux, you can find this with `ip addr`.
        *   On macOS, you can find this with `ifconfig`.
    *   `SERIAL_PORT_KEYWORD`: A string used to find the modem's serial port. The default `"acm"` works for many modems on Linux (`/dev/ttyACM*`) and macOS (`/dev/cu.usbmodem*`). You may need to adjust this based on the output of `ls /dev` or `dmesg`.
    *   `APN`: The Access Point Name for your mobile carrier (e.g., "internet").

    **Optional settings:**
    *   `DNS_OVERRIDE`: If you want to use specific DNS servers instead of the ones provided by the carrier, set them here (e.g., `['8.8.8.8', '1.1.1.1']`).

## Usage

The script must be run with `sudo` because it needs permission to modify your system's network settings.

**To connect and monitor the connection:**
