import time
import logging
import sys
import os
import platform
import threading # Added import
from datetime import datetime, timezone # Added timezone for UTC
from typing import Optional # Ensure Optional is imported

# Project modules
import config
from modem_manager import ModemManager, ModemError
from network_manager import NetworkManager, NetworkError
from ui_utils import Spinner, get_bars, hide_cursor, show_cursor, clear_screen, print_at, clear_lines_from
from converters import (
    get_band_lte, get_bandwidth_mhz, convert_rsrp_to_rssi,
    parse_at_response_value, parse_nullable_int, parse_nullable_float
)
from monitoring import SerialPortMonitor, NetworkMonitor

# --- Logger Setup ---
# Main Application Logger
if not os.path.exists(config.LOG_DIR):
    os.makedirs(config.LOG_DIR)

main_log_file_timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
main_log_filename = os.path.join(config.LOG_DIR, f"{config.LOG_FILE_BASENAME}-{main_log_file_timestamp}.txt")

logging.basicConfig(
    level=config.LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(main_log_filename),
        logging.StreamHandler(sys.stdout) # Also print to console
    ]
)
logger = logging.getLogger(__name__)

# AT Command Logger Setup
if not os.path.exists(config.AT_LOG_DIR):
    os.makedirs(config.AT_LOG_DIR)

at_log_filename_utc = datetime.now(timezone.utc).strftime(config.AT_LOG_FILENAME_FORMAT)
at_log_filepath = os.path.join(config.AT_LOG_DIR, at_log_filename_utc)

at_logger = logging.getLogger(config.AT_LOGGER_NAME)
at_logger.setLevel(logging.DEBUG) # Capture all AT command details
at_formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S.%f') # Simple format
at_file_handler = logging.FileHandler(at_log_filepath, mode='w') # New file for each run
at_file_handler.setFormatter(at_formatter)
at_logger.addHandler(at_file_handler)
at_logger.propagate = False # Prevent AT logs from going to the main logger's handlers

logger.info(f"Main application log: {main_log_filename}")
logger.info(f"AT command log: {at_log_filepath}")
# --- End Logger Setup ---


class App:
    def __init__(self, only_monitor_mode=False):
        self.modem = ModemManager()
        self.network = NetworkManager()
        self.only_monitor = only_monitor_mode
        self.app_running = True
        self.serial_monitor: Optional[SerialPortMonitor] = None
        self.network_monitor: Optional[NetworkMonitor] = None
        self.watchdog_event = threading.Event() # Used to signal main loop to break from monitors

        # For status display
        self.status_start_line = 0 # Line number where status display begins

    def _setup_terminal(self):
        # clear_screen() # Initial clear
        # Set window title (platform dependent, often hard for console)
        # sys.stdout.write(f"\033]0;{config.APP_VERSION}{' (monitor)' if self.only_monitor else ''}\a")
        # sys.stdout.flush()
        logger.info(f"=== {config.APP_VERSION} {'(monitor)' if self.only_monitor else ''} ===")
        hide_cursor()

    def _cleanup_terminal(self):
        show_cursor()

    def _parse_modem_info(self, at_cmd_output: list) -> dict:
        info = {}
        info['manufacturer'] = parse_at_response_value(at_cmd_output, '+CGMI:', item_index=1)
        info['model'] = parse_at_response_value(at_cmd_output, '+FMM:', item_index=1) # Fibocom specific?
        info['firmware_ver'] = parse_at_response_value(at_cmd_output, '+GTPKGVER:', item_index=1) # Fibocom specific?
        info['serial_number'] = parse_at_response_value(at_cmd_output, '+CFSN:', item_index=1)
        info['imei'] = parse_at_response_value(at_cmd_output, '+CGSN:', item_index=1)
        return info

    def _parse_sim_info(self, at_cmd_output: list) -> dict:
        info = {}
        
        # Try parsing IMSI assuming format "+CIMI: <value>" first (like PowerShell script implies)
        imsi_val = parse_at_response_value(at_cmd_output, filter_keyword='+CIMI:', item_index=1)
        
        # If not found, try finding a purely numeric line (as comment "CIMI response is just the number" suggests)
        if not imsi_val:
            for line_content in at_cmd_output:
                stripped_line = line_content.strip()
                # Standard IMSIs are 15 digits. Some test/dummy ones might be shorter or different.
                if stripped_line.isdigit() and (14 <= len(stripped_line) <= 15):
                    imsi_val = stripped_line
                    logger.debug(f"Found IMSI as a numeric line: {imsi_val}")
                    break
        info['imsi'] = imsi_val

        # CCID parsing (assuming it's like +CCID: <value>)
        info['ccid'] = parse_at_response_value(at_cmd_output, filter_keyword='+CCID:', item_index=1)
        return info

    def _parse_ip_info(self, at_cmd_output: list) -> dict:
        info = {'ip_addr': None, 'ip_mask': None, 'ip_gw': None, 'ip_dns': []}
        # This function now parses CGCONTRDP, CGPADDR, and XDNS responses.
        # It iterates through the lines for each possible response type.

        # +CGCONTRDP: 1,5,"internet","10.172.128.205.255.255.255.252","10.172.128.206","10.11.12.13","10.11.12.14",...
        for line in at_cmd_output:
            if "+CGCONTRDP:" in line:
                parts = line.split(',')
                if len(parts) >= 4: # Index 3 for ip_mask_combined
                    ip_mask_combined = parts[3].strip().replace('"', '')
                    ip_parts = ip_mask_combined.split('.')
                    if len(ip_parts) == 8: # IPv4 with mask
                        info['ip_addr'] = ".".join(ip_parts[0:4])
                        info['ip_mask'] = ".".join(ip_parts[4:8])
                    elif len(ip_parts) == 4: # Just IP, no mask in this part
                        info['ip_addr'] = ip_mask_combined
                if len(parts) >= 5 and not info['ip_gw']: # Gateway (index 4)
                     gw_candidate = parts[4].strip().replace('"', '')
                     if gw_candidate and gw_candidate.lower() != '0.0.0.0':
                        info['ip_gw'] = gw_candidate
                if len(parts) >= 6: # DNS1 (index 5)
                    dns1_candidate = parts[5].strip().replace('"', '')
                    if dns1_candidate and dns1_candidate.lower() != '0.0.0.0':
                        info['ip_dns'].append(dns1_candidate)
                if len(parts) >= 7: # DNS2 (index 6)
                    dns2_candidate = parts[6].strip().replace('"', '')
                    if dns2_candidate and dns2_candidate.lower() != '0.0.0.0':
                         info['ip_dns'].append(dns2_candidate)
                # Do not break, as other lines might contain other info (e.g. XDNS)
        
        # +CGPADDR: 1,"10.172.128.205"
        if not info['ip_addr']: # Only parse if not already found via CGCONTRDP
            for line in at_cmd_output:
                if "+CGPADDR:" in line:
                    # Format is +CGPADDR: <cid>,<ip_addr>
                    parts = line.split(',')
                    if len(parts) > 1:
                        ip_part = parts[1].strip().replace('"', '')
                        if ip_part:
                            info['ip_addr'] = ip_part
                            break # Found IP, stop looking for it

        # +XDNS: 1, "8.8.8.8", "8.8.4.4"
        if not info['ip_dns']: # Only parse if not already found via CGCONTRDP
            for line in at_cmd_output:
                if "+XDNS:" in line:
                    parts = line.split(',')
                    # parts[0] is '+XDNS: 1'
                    # parts[1] is ' "dns1"'
                    # parts[2] is ' "dns2"'
                    if len(parts) > 1:
                        dns1 = parts[1].strip().replace('"', '')
                        if dns1: info['ip_dns'].append(dns1)
                    if len(parts) > 2:
                        dns2 = parts[2].strip().replace('"', '')
                        if dns2: info['ip_dns'].append(dns2)
                    break # Found DNS, stop looking for it

        return info
        
    def _display_status(self, status_data: dict):
        # This function needs to clear previous status lines and print new ones
        # For simplicity, it will just print. A more advanced UI would use curses or direct cursor manipulation.
        
        # If it's the first time, record where the status display starts
        # This is a simplified approach. A robust TUI needs better line management.
        # For now, we'll just print sequentially. If using clear_lines_from, need to know start.
        # print_at(self.status_start_line, 1, "") # Move to start
        # clear_lines_from(self.status_start_line) # Clear old status

        logger.info("--- Status Update ---") # Use logger for now, replace with direct print for TUI
        
        def format_line(label, value, bar_data=None):
            line = f"{label:<17} {str(value if value is not None else '--'):<20}"
            if bar_data and value is not None: # Only show bar if value is not None
                bar_value, bar_min, bar_max = bar_data
                line += f" {get_bars(bar_value, bar_min, bar_max)}"
            return line

        print(format_line("Operator:", f"{status_data.get('oper', '--')} ({status_data.get('mode', '--')})"))
        if status_data.get('temp') is not None:
            print(format_line("Temp:", f"{status_data['temp']} °C"))

        if status_data.get('mode'): # If we have a mode (UMTS/LTE)
            print(format_line("Distance:", f"{status_data.get('distance_km', '--')} km"))
            print(format_line("Signal:", f"{status_data.get('csq_perc', 0.0):.0f}%",
                              bar_data=(status_data.get('csq_perc'), 0, 100) if status_data.get('csq_perc') is not None else None))

        mode = status_data.get('mode')
        if mode == 'UMTS':
            print(format_line("RSSI (UMTS):", f"{status_data.get('u_rssi', '--')} dBm", # u_rssi might not be populated by XMCI
                              bar_data=(status_data.get('u_rssi'), -120, -25) if status_data.get('u_rssi') is not None else None))
            print(format_line("RSCP:", f"{status_data.get('u_rscp', '--')} dBm",
                              bar_data=(status_data.get('u_rscp'), -120, -25) if status_data.get('u_rscp') is not None else None))
            print(format_line("EC/NO:", f"{status_data.get('u_ecno', '--')} dB",
                              bar_data=(status_data.get('u_ecno'), -24, 0) if status_data.get('u_ecno') is not None else None))
            print(format_line("UARFCN:", status_data.get('u_dluarfcn', '--')))
        elif mode == 'LTE':
            print(format_line("RSSI (LTE):", f"{status_data.get('rssi', '--')} dBm",
                              bar_data=(status_data.get('rssi'), -110, -25) if status_data.get('rssi') is not None else None))
            print(format_line("SINR:", f"{status_data.get('sinr', '--')} dB",
                              bar_data=(status_data.get('sinr'), -10, 30) if status_data.get('sinr') is not None else None))
            print(format_line("RSRP:", f"{status_data.get('rsrp', '--')} dBm",
                              bar_data=(status_data.get('rsrp'), -120, -50) if status_data.get('rsrp') is not None else None))
            print(format_line("RSRQ:", f"{status_data.get('rsrq', '--')} dB",
                              bar_data=(status_data.get('rsrq'), -25, -1) if status_data.get('rsrq') is not None else None))
            print(format_line("Band:", status_data.get('band_info', '--')))
            print(format_line("EARFCN:", status_data.get('dluarfcn', '--')))

            # Carrier Aggregation Info
            carriers = status_data.get('carriers', [])
            for i, carrier in enumerate(carriers):
                print(f"  Carrier {i+1}: CI: {carrier.get('ci','--')}, PCI: {carrier.get('pci','--')}, "
                      f"Band: {carrier.get('band','--')} ({carrier.get('earfcn','--')}), "
                      f"RSRP: {carrier.get('rsrp','--')}dBm, RSRQ: {carrier.get('rsrq','--')}dB")
        sys.stdout.flush()


    def run_status_loop(self):
        logger.info("Starting status monitoring loop...")
        # self.status_start_line = get_current_cursor_line() # Needs a way to get current line

        while not self.watchdog_event.is_set():
            status_data = {}
            try:
                # Basic commands for status
                # Temperature (check model as in PS)
                model_name = self.modem.get_modem_port_details().get('model', '') # Relies on modem_details being populated
                if not model_name and self.modem.modem_details: # Fallback if 'model' key isn't there but details exist
                    model_name = self.modem.modem_details.get('product', '') # Try 'product' from pyserial list_ports
                
                if 'L850' not in model_name: # Example check
                    try:
                        mtsm_resp = self.modem.send_at_command("AT+MTSM=1", timeout=5)
                        status_data['temp'] = parse_nullable_int(parse_at_response_value(mtsm_resp, '+MTSM:', item_index=1))
                    except ModemError as e:
                        logger.debug(f"Could not get temperature: {e}")


                cops_resp = self.modem.send_at_command("AT+COPS?", timeout=5)
                raw_act = parse_at_response_value(cops_resp, '+COPS:', item_index=4) 
                if raw_act is None: 
                    raw_act = parse_at_response_value(cops_resp, '+COPS:', item_index=3) # Fallback for different COPS formats
                
                act = parse_nullable_int(raw_act)
                # Operator name parsing: try index 2 if index 3 is numeric (PLMN)
                oper_candidate = parse_at_response_value(cops_resp, '+COPS:', item_index=3)
                if oper_candidate and oper_candidate.replace('"','').isdigit(): # If it's a number (PLMN)
                    status_data['oper'] = parse_at_response_value(cops_resp, '+COPS:', item_index=2)
                else:
                    status_data['oper'] = oper_candidate


                mode_map = {0: 'GSM/EDGE', 2: 'UMTS', 3: 'LTE', 4: 'HSDPA', 5: 'HSUPA', 6: 'HSPA', 7: 'LTE'} 
                status_data['mode'] = mode_map.get(act)

                csq_resp = self.modem.send_at_command("AT+CSQ", timeout=5)
                csq_val_str = parse_at_response_value(csq_resp, '+CSQ:', item_index=1) 
                if csq_val_str:
                    csq_rssi_val = parse_nullable_int(csq_val_str.split(',')[0])
                    if csq_rssi_val is not None and csq_rssi_val != 99:
                        status_data['csq_perc'] = (csq_rssi_val / 31.0) * 100.0
                    else:
                        status_data['csq_perc'] = 0.0
                
                combined_ext_info_resp = self.modem.send_at_command("AT+XCCINFO?;+XLEC?;+XMCI=1", timeout=10)

                pcc_dl_bw_code_str = parse_at_response_value(combined_ext_info_resp, '+XLEC:', item_index=2, split_chars=',')
                pcc_dl_bw_code = parse_nullable_int(pcc_dl_bw_code_str)
                status_data['bw_code'] = pcc_dl_bw_code

                for line in combined_ext_info_resp:
                    if line.startswith("+XMCI:"):
                        logger.debug(f"Raw XMCI line: {line}")
                        parts = line.split(',')
                        if not parts or len(parts) < 2:
                            continue

                        first_segment = parts[0]
                        rat_str = ""
                        if ":" in first_segment:
                            rat_str = first_segment.split(':', 1)[-1].strip()

                        # Expected format for +XMCI: <rat>,<MCC>,<MNC>,<LAC/TAC>,<CI>,<PCI/PSC/BSIC>,<DL_ARFCN>,<UL_ARFCN>,<Placeholder>,<RSRP/RSCP_raw>,<RSRQ/EcNo_raw>,<SINR_raw>,<TA_raw>,...
                        # Indices relative to `parts = line.split(',')`
                        # parts[0] = "+XMCI: <rat>"
                        # parts[1] = MCC
                        # parts[2] = MNC
                        # parts[3] = LAC/TAC
                        # parts[4] = CI
                        # parts[5] = PCI/PSC/BSIC
                        # parts[6] = DL_ARFCN (EARFCN-DL or UARFCN-DL)
                        # parts[7] = UL_ARFCN
                        # parts[8] = Placeholder (e.g., "0xFFFFFFFF")
                        # parts[9] = RSRP_raw (LTE) or RSCP_raw (UMTS)
                        # parts[10] = RSRQ_raw (LTE) or EcNo_raw (UMTS)
                        # parts[11] = SINR_raw (LTE)
                        # parts[12] = TA_raw (LTE)

                        if rat_str == "4" and (act == 7 or act == 3):  # LTE
                            logger.debug(f"Parsing LTE XMCI line: {line}")
                            if len(parts) > 6: # DL_ARFCN
                                status_data['dluarfcn'] = parse_nullable_int(parts[6].strip().replace('"', ''))
                            if len(parts) > 9: # RSRP_raw
                                rsrp_raw = parse_nullable_float(parts[9].strip().replace('"', ''))
                                if rsrp_raw is not None: status_data['rsrp'] = rsrp_raw - 141
                            if len(parts) > 10: # RSRQ_raw
                                rsrq_raw = parse_nullable_float(parts[10].strip().replace('"', ''))
                                if rsrq_raw is not None: status_data['rsrq'] = (rsrq_raw / 2.0) - 20
                            if len(parts) > 11: # SINR_raw
                                sinr_raw = parse_nullable_float(parts[11].strip().replace('"', ''))
                                if sinr_raw is not None: status_data['sinr'] = sinr_raw / 2.0
                            if len(parts) > 12: # TA_raw
                                ta_val = parse_nullable_int(parts[12].strip().replace('"', ''))
                                if ta_val is not None and ta_val != 65535 and ta_val != 255: # 255 is often invalid TA
                                    status_data['distance_km'] = round((ta_val * 78.125) / 1000, 3)
                                elif ta_val is not None:
                                     logger.debug(f"Invalid TA value for LTE received: {ta_val}")
                            
                            status_data['rssi'] = convert_rsrp_to_rssi(status_data.get('rsrp'), pcc_dl_bw_code)
                            band_val = get_band_lte(status_data.get('dluarfcn'))
                            bw_mhz = get_bandwidth_mhz(pcc_dl_bw_code)
                            if band_val != "--" and bw_mhz is not None:
                                status_data['band_info'] = f"{band_val} @ {bw_mhz}MHz"
                            else:
                                status_data['band_info'] = band_val if band_val != "--" else (f"{bw_mhz}MHz" if bw_mhz is not None else "--")
                            # Only process first relevant XMCI line for serving cell
                            break 
                        
                        elif rat_str == "2" and act == 2:  # UMTS
                            logger.debug(f"Parsing UMTS XMCI line: {line}")
                            if len(parts) > 6: # DL_ARFCN (UARFCN-DL)
                                status_data['u_dluarfcn'] = parse_nullable_int(parts[6].strip().replace('"', ''))
                            if len(parts) > 9: # RSCP_raw
                                rscp_raw = parse_nullable_float(parts[9].strip().replace('"', ''))
                                if rscp_raw is not None: 
                                    status_data['u_rscp'] = rscp_raw - 121
                                    status_data['u_rssi'] = rscp_raw - 111 # As per PS script logic
                            if len(parts) > 10: # EcNo_raw
                                ecno_raw = parse_nullable_float(parts[10].strip().replace('"', ''))
                                if ecno_raw is not None: status_data['u_ecno'] = (ecno_raw / 2.0) - 24
                            # Only process first relevant XMCI line
                            break

                self._display_status(status_data)

            except ModemError as e:
                logger.error(f"Modem error during status update: {e}")
                if not self.modem.is_port_available():
                    self.watchdog_event.set() 
                    logger.error("Modem port seems unavailable. Stopping status loop.")
            except Exception as e:
                logger.error(f"Unexpected error during status update: {e}", exc_info=True)
            
            if self.watchdog_event.is_set():
                break
            time.sleep(2) # Update interval


    def start_monitors(self):
        self.watchdog_event.clear() # Ensure it's clear before starting

        self.serial_monitor = SerialPortMonitor(self.modem)
        self.serial_monitor.start()
        
        if not self.only_monitor and self.network.interface_name:
            self.network_monitor = NetworkMonitor(self.network)
            self.network_monitor.start()
        
        # Thread to wait for any monitor to signal disconnection
        def _watchdog_waiter():
            while self.app_running and not self.watchdog_event.is_set():
                if self.serial_monitor and self.serial_monitor.disconnected_event.is_set():
                    logger.warning("Serial monitor signaled disconnection.")
                    self.watchdog_event.set()
                    break
                if self.network_monitor and self.network_monitor.disconnected_event.is_set():
                    logger.warning("Network monitor signaled disconnection.")
                    self.watchdog_event.set()
                    break
                time.sleep(0.5) # Check frequently
            logger.debug("Watchdog waiter thread finished.")

        threading.Thread(target=_watchdog_waiter, daemon=True).start()


    def stop_monitors(self):
        if self.serial_monitor:
            self.serial_monitor.stop()
            self.serial_monitor.join(timeout=2)
            self.serial_monitor = None
        if self.network_monitor:
            self.network_monitor.stop()
            self.network_monitor.join(timeout=2)
            self.network_monitor = None
        self.watchdog_event.set() # Ensure main loop also knows to stop if it was waiting

    def main_loop(self):
        self._setup_terminal()
        
        while self.app_running:
            port_info = None
            modem_port_details = None
            try:
                # 1. Find and Open Modem Port
                with Spinner("Searching for modem control port..."):
                    while self.app_running: # Loop until port found or app exits
                        port_info_tuple = self.modem.find_serial_port()
                        if port_info_tuple:
                            port_info, modem_port_details = port_info_tuple
                            self.modem.set_modem_port_details(modem_port_details or {}) # Store details
                            break
                        if not self.app_running: return # Exit if app stopped during search
                        time.sleep(5)
                if not port_info: # Should not happen if loop broken by finding port_info
                    logger.error("Could not find modem port after search. Exiting.")
                    return

                with Spinner(f"Opening modem control port {port_info}..."):
                    if not self.modem.open_port(port_info):
                        raise ModemError(f"Failed to open port {port_info}")

                # Basic AT commands
                self.modem.send_at_command("ATE0") # Echo off, to match successful log
                self.modem.send_at_command("AT+CMEE=1") # Numeric errors, to match successful log

                # 2. Get Modem Information
                logger.info("=== Modem Information ===")
                modem_info_resp = self.modem.send_at_command("AT+CGMI?;+FMM?;+GTPKGVER?;+CFSN?;+CGSN?", timeout=10)
                modem_data = self._parse_modem_info(modem_info_resp)
                self.modem.modem_details.update(modem_data) # Store parsed model, fw etc.
                for key, val in modem_data.items(): logger.info(f"{key.replace('_', ' ').title()}: {val}")

                if not self.only_monitor:
                    sim_ready_resp = self.modem.send_at_command("AT+CPIN?")
                    if "+CPIN: READY" not in "".join(sim_ready_resp).upper(): # Check case-insensitively
                        logger.error(f"SIM card not ready or error: {sim_ready_resp}")
                        raise ModemError("SIM not ready")

                sim_info_resp = self.modem.send_at_command("AT+CIMI?;+CCID?", timeout=10)
                sim_data = self._parse_sim_info(sim_info_resp)
                for key, val in sim_data.items(): logger.info(f"{key.upper()}: {val if val else '--'}")

                # 3. Connect (if not in only_monitor mode)
                if not self.only_monitor:
                    with Spinner("Initializing connection..."):
                        # Sequence inspired by successful logs from other devices
                        self.modem.send_at_command("AT+CFUN=1")
                        self.modem.send_at_command("AT+CGPIAF=1,0,0,0")
                        self.modem.send_at_command("AT+CREG=0") 
                        self.modem.send_at_command("AT+CEREG=0")
                        
                        try:
                            logger.info("Detaching GPRS (AT+CGATT=0)...")
                            self.modem.send_at_command("AT+CGATT=0")
                        except ModemError as e:
                            logger.warning(f"AT+CGATT=0 returned an error (possibly already detached), proceeding: {e}")

                        try:
                            logger.info("Deregistering from network (AT+COPS=2)...")
                            self.modem.send_at_command("AT+COPS=2")
                        except ModemError as e:
                            logger.warning(f"AT+COPS=2 returned an error (possibly already deregistered), proceeding: {e}")
                        
                        # Deactivating contexts based on successful log sequence
                        try:
                            logger.info("Querying active profiles with AT+XACT=?...")
                            xact_query_resp = self.modem.send_at_command("AT+XACT=?")
                            profiles_to_deactivate = ""
                            for line in xact_query_resp:
                                if line.startswith("+XACT:"):
                                    # Format is +XACT: (range),(range),profile1,profile2,...
                                    content = line.split(":", 1)[1].strip()
                                    parts = content.split(',')
                                    profile_parts = []
                                    # Filter out the range parts in parentheses
                                    for part in parts:
                                        # The working log does not deactivate profile 0. Replicating that behavior.
                                        if '(' not in part and ')' not in part and part.strip() != '0':
                                            profile_parts.append(part.strip())
                                    if profile_parts:
                                        profiles_to_deactivate = ",".join(profile_parts)
                                    break
                            
                            if profiles_to_deactivate:
                                logger.info(f"Deactivating profiles (excluding 0): {profiles_to_deactivate}")
                                self.modem.send_at_command(f"AT+XACT=2,,,{profiles_to_deactivate}")
                            else:
                                logger.warning("Could not parse profiles from AT+XACT=? response, or only profile 0 was active. No profiles will be deactivated.")
                        except Exception as e:
                            logger.warning(f"Failed to perform dynamic profile deactivation. Error: {e}", exc_info=True)

                        try:
                            logger.info("Attempting to clear PDP context 0 (AT+CGDCONT=0,\"IP\")...")
                            self.modem.send_at_command("AT+CGDCONT=0,\"IP\"")
                        except ModemError as e:
                            logger.warning(f"AT+CGDCONT=0,\"IP\" failed (possibly not set or supported), proceeding: {e}")
                        try:
                            logger.info("Attempting to clear PDP context 0 (AT+CGDCONT=0)...")
                            self.modem.send_at_command("AT+CGDCONT=0")
                        except ModemError as e:
                            logger.warning(f"AT+CGDCONT=0 failed (possibly not set or supported), proceeding: {e}")
                        
                        logger.info(f"Setting PDP context 1 for APN: {config.APN}")
                        self.modem.send_at_command(f"AT+CGDCONT=1,\"IP\",\"{config.APN}\"")
                        
                        if config.APN_USER or config.APN_PASS:
                             logger.info("Setting APN authentication (PAP/CHAP)...")
                             self.modem.send_at_command(f"AT+XGAUTH=1,1,\"{config.APN_USER}\",\"{config.APN_PASS}\"")
                        else:
                             logger.info("Setting APN authentication to NONE for context 1...")
                             self.modem.send_at_command(f"AT+XGAUTH=1,0,\"\",\"\"")
                        
                        logger.info("Setting data channel for NCM mode (AT+XDATACHANNEL)...")
                        # Using /USBCDC/0 as seen in successful logs for non-Windows systems
                        self.modem.send_at_command("AT+XDATACHANNEL=1,1,\"/USBCDC/0\",\"/USBHS/NCM/0\",2,1")
                        
                        logger.info("Setting DNS acquisition mode (AT+XDNS)...")
                        self.modem.send_at_command("AT+XDNS=1,1")

                        logger.info("Activating PDP context 1 (AT+CGACT=1,1)...")
                        self.modem.send_at_command("AT+CGACT=1,1") 
                        
                        logger.info("Setting automatic operator selection (AT+COPS=0)...")
                        self.modem.send_at_command("AT+COPS=0", timeout=config.AT_COMMAND_LONG_TIMEOUT) 
                        
                        logger.info("Attaching to GPRS network (AT+CGATT=1)...")
                        self.modem.send_at_command("AT+CGATT=1", timeout=config.AT_COMMAND_LONG_TIMEOUT)

                        logger.info("Starting data session (AT+CGDATA=M-RAW_IP,1)...")
                        # This command returns OK, and then CONNECT is sent as a URC. Expect OK.
                        self.modem.send_at_command("AT+CGDATA=\"M-RAW_IP\",1", expect_ok=True)

                    with Spinner("Establishing connection..."):
                        # Loop to wait for network registration, which is a prerequisite for a valid CSQ
                        registration_timeout = 30  # seconds
                        start_time = time.monotonic()
                        registered = False
                        while time.monotonic() - start_time < registration_timeout:
                            if not self.app_running: return

                            try:
                                # Check EPS registration status first, as it's primary for LTE
                                reg_resp = self.modem.send_at_command("AT+CEREG?")
                                # Response is like +CEREG: <n>,<stat>[,<tac>,<ci>...]
                                # We care about <stat>, which is the second parameter after the colon.
                                stat_val_str = parse_at_response_value(reg_resp, '+CEREG:', item_index=2, split_chars='[:,]')
                                stat_val = parse_nullable_int(stat_val_str)
                                
                                logger.debug(f"Registration status check: CEREG={stat_val}")
                                # 1: registered, home network. 5: registered, roaming.
                                if stat_val in [1, 5]:
                                    logger.info(f"Modem registered on network (CEREG status: {stat_val}).")
                                    registered = True
                                    break
                            except (ModemError, IndexError, TypeError) as e:
                                logger.warning(f"Could not get/parse registration status, will retry: {e}")

                            time.sleep(2)

                        if not registered:
                            raise NetworkError("Failed to register on the network within the timeout period.")

                        # Now that we are registered, the CSQ should be valid.
                        # The original loop was getting stuck here. Let's just do one check.
                        cgatt_csq_resp = self.modem.send_at_command("AT+CGATT?; +CSQ?")
                        csq_val_str = parse_at_response_value(cgatt_csq_resp, '+CSQ:', item_index=1)
                        csq_val = parse_nullable_int(csq_val_str.split(',')[0] if csq_val_str else None)
                        if csq_val is None or csq_val == 99:
                            logger.warning(f"CSQ value is still not valid ({csq_val}) after registration. This might cause issues, but proceeding.")
                        else:
                            logger.info(f"Signal quality is valid (CSQ: {csq_val}). Connection established.")

                # 4. Get Connection Information
                logger.info("=== Connection Information ===")
                
                # Get IP address using CGPADDR, as per working logs
                logger.info("Querying IP address with AT+CGPADDR=1...")
                cgpaddr_resp = self.modem.send_at_command("AT+CGPADDR=1")
                
                # Get DNS servers using XDNS?, as per working logs
                logger.info("Querying DNS servers with AT+XDNS?...")
                xdns_resp = self.modem.send_at_command("AT+XDNS?")

                # Combine responses and parse
                combined_ip_info_resp = cgpaddr_resp + xdns_resp
                ip_data = self._parse_ip_info(combined_ip_info_resp)

                # The working log implies a /32 mask (255.255.255.255) is needed for a stable point-to-point link.
                # The modem sometimes returns a wide subnet mask (e.g., /8) which can cause routing issues on the host OS.
                # To align with the working configuration, we will force a /32 mask and use the IP address itself
                # as the gateway, which is a common setup for cellular P-t-P links.
                if ip_data.get('ip_addr'):
                    original_mask = ip_data.get('ip_mask', 'N/A')
                    original_gw = ip_data.get('ip_gw', 'N/A')
                    
                    new_mask = '255.255.255.255'
                    new_gw = ip_data['ip_addr'] # Use the IP as the gateway for the route command

                    if original_mask != new_mask or original_gw != new_gw:
                        logger.info("Forcing point-to-point link configuration for compatibility:")
                        logger.info(f"  - IP Address:    {ip_data['ip_addr']}")
                        logger.info(f"  - Original Mask: {original_mask}, New Mask: {new_mask}")
                        logger.info(f"  - Original GW:   {original_gw}, New GW: {new_gw}")
                        ip_data['ip_mask'] = new_mask
                        ip_data['ip_gw'] = new_gw

                logger.info(f"IP Address: {ip_data.get('ip_addr', '--')}")
                logger.info(f"Subnet Mask: {ip_data.get('ip_mask', '--')}")
                logger.info(f"Gateway: {ip_data.get('ip_gw', '--')}")
                
                final_dns = config.DNS_OVERRIDE if config.DNS_OVERRIDE else ip_data.get('ip_dns', [])
                for i, dns in enumerate(final_dns): logger.info(f"DNS{i+1}: {dns}")

                # 5. Setup Network (if not in only_monitor mode)
                if not self.only_monitor:
                    if ip_data.get('ip_addr') and ip_data.get('ip_mask') and ip_data.get('ip_gw'):
                        with Spinner("Finding network interface..."):
                            iface_name = self.network.find_network_interface_by_mac(config.MODEM_MAC_ADDRESS)
                            if not iface_name:
                                logger.error(f"Could not find network interface with MAC {config.MODEM_MAC_ADDRESS}")
                                raise NetworkError("Network interface not found")
                        
                        logger.info(f"Attempting to configure network on interface: {iface_name}")
                        logger.info(f"  Using IP Address: {ip_data['ip_addr']}")
                        logger.info(f"  Using Subnet Mask: {ip_data['ip_mask']}")
                        logger.info(f"  Using Gateway: {ip_data['ip_gw']}")
                        logger.info(f"  Using DNS Servers: {final_dns}")

                        with Spinner(f"Setup network on {iface_name}..."):
                            if not self.network.initialize_network(
                                ip_data['ip_addr'], ip_data['ip_mask'], ip_data['ip_gw'], final_dns
                            ):
                                # Error messages from initialize_network will be logged by NetworkManager
                                raise NetworkError("Failed to initialize network interface. Check logs for details (e.g., permissions for route, or IP/Mask/Gateway mismatch).")
                    else:
                        logger.error("Insufficient IP information from modem to configure network.")
                        if not self.only_monitor: 
                            raise NetworkError("Cannot configure network, missing IP details.")
                
                # 6. Start Watchdog Monitors and Status Loop
                self.start_monitors()
                logger.info("=== Status ===")
                self.run_status_loop() # This loop breaks when watchdog_event is set

            except ModemError as e:
                logger.error(f"Modem operation failed: {e}")
            except NetworkError as e:
                logger.error(f"Network operation failed: {e}")
            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received. Exiting.")
                self.app_running = False
            except Exception as e:
                logger.error(f"An unexpected error occurred in main_loop: {e}", exc_info=True)
            finally:
                self.stop_monitors()
                if self.modem:
                    self.modem.close_port()
            
            if self.app_running: 
                logger.info("Restarting process in 5 seconds...")
                time.sleep(5)
            else:
                break 
        
        self._cleanup_terminal()
        logger.info("Application terminated.")


if __name__ == "__main__":
    only_monitor_arg = "-OnlyMonitor".lower() in [arg.lower() for arg in sys.argv]
    
    app = App(only_monitor_mode=only_monitor_arg)
    try:
        app.main_loop()
    except KeyboardInterrupt:
        logger.info("Application interrupted by user. Shutting down.")
    except Exception as e:
        logger.critical(f"Unhandled exception in application: {e}", exc_info=True)
    finally:
        app.app_running = False 
        app.stop_monitors() 
        if app.modem:
            app.modem.close_port()
        show_cursor() 
