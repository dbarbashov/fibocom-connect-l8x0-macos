import serial
import serial.tools.list_ports
import time
import threading
import logging
from typing import List, Optional, Tuple

from config import SERIAL_PORT_KEYWORD, AT_COMMAND_TIMEOUT, AT_LOGGER_NAME

logger = logging.getLogger(__name__)
# Get the dedicated AT command logger, configured in main.py
at_command_logger = logging.getLogger(AT_LOGGER_NAME)

class ModemError(Exception):
    pass

class ModemManager:
    def __init__(self):
        self.port: Optional[serial.Serial] = None
        self.port_name: Optional[str] = None
        self.modem_details = {} # To store container_id or other identifiers if found

    def find_serial_port(self) -> Optional[Tuple[str, dict]]:
        """
        Finds a serial port that matches SERIAL_PORT_KEYWORD.
        Returns a tuple (port_name, details_dict) or None.
        'details_dict' can store things like USB VID/PID for more specific matching.
        """
        logger.info(f"Searching for serial port containing keyword: '{SERIAL_PORT_KEYWORD}'")
        ports = serial.tools.list_ports.comports()
        for p in ports:
            logger.debug(f"Found port: {p.device}, description: {p.description}, hwid: {p.hwid}")
            # Basic matching, can be improved with VID/PID checks
            if SERIAL_PORT_KEYWORD.lower() in p.description.lower() or \
               SERIAL_PORT_KEYWORD.lower() in p.device.lower() or \
               SERIAL_PORT_KEYWORD.lower() in p.hwid.lower():
                logger.info(f"Matched port: {p.device}")
                # Attempt to get more details, e.g., for linking to network interface
                # This is highly OS-dependent. For now, just store basic info.
                details = {
                    "device": p.device,
                    "description": p.description,
                    "hwid": p.hwid,
                    "vid": p.vid,
                    "pid": p.pid,
                    "serial_number": p.serial_number,
                    "location": p.location,
                    "manufacturer": p.manufacturer,
                    "product": p.product,
                    "interface": p.interface,
                }
                # The concept of "ContainerID" is Windows-specific.
                # We might need to use USB device path or VID/PID to uniquely identify
                # the modem and its associated network interface on Linux/macOS.
                # For now, we'll primarily rely on the MAC address for the network interface.
                return p.device, details
        logger.warning("No matching serial port found.")
        return None

    def open_port(self, port_name: str) -> bool:
        if self.port and self.port.is_open:
            if self.port.name == port_name:
                logger.info(f"Port {port_name} is already open.")
                return True
            else:
                self.close_port()
        
        logger.info(f"Opening serial port: {port_name}")
        try:
            self.port = serial.Serial(
                port_name,
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1, # Read timeout
                write_timeout=1
            )
            # DTR/RTS handling might be needed depending on the modem
            # Toggle DTR to wake up some modems
            self.port.dtr = False
            time.sleep(0.2)
            self.port.dtr = True
            self.port.rts = True # Typically DTR is enough, but some modems might need RTS
            
            time.sleep(2) # Increased delay for modem/port to settle

            self.port.reset_input_buffer()
            self.port.reset_output_buffer()
            self.port_name = port_name
            logger.info(f"Port {port_name} opened successfully.")
            return True
        except serial.SerialException as e:
            logger.error(f"Failed to open port {port_name}: {e}")
            self.port = None
            return False

    def close_port(self):
        if self.port and self.port.is_open:
            logger.info(f"Closing serial port: {self.port.name}")
            try:
                self.port.close()
            except Exception as e:
                logger.error(f"Error closing port {self.port.name}: {e}")
            self.port = None
            self.port_name = None
        else:
            logger.info("No port to close or port already closed.")

    def send_at_command(self, command: str, timeout: Optional[float] = None, expect_ok: bool = True) -> List[str]:
        if not self.port or not self.port.is_open:
            raise ModemError("Serial port not open.")

        actual_timeout = timeout if timeout is not None else AT_COMMAND_TIMEOUT
        
        logger.debug(f"--> {command} (timeout: {actual_timeout}s)")
        at_command_logger.info(f"--> {command}")
        
        # It's generally better to reset buffers just before write/read,
        # but if issues persist with stale data, this can be done here too.
        # self.port.reset_input_buffer() 

        try:
            self.port.write(f"{command}\r\n".encode('utf-8'))
            self.port.flush()
        except serial.SerialTimeoutException as e:
            logger.error(f"Timeout writing to serial port for command: {command}")
            at_command_logger.error(f"Timeout writing to serial port for command: {command}")
            raise ModemError(f"Write timeout for command: {command}") from e
        except serial.SerialException as e: # Catch other serial write errors
            logger.error(f"Error writing to serial port: {e}")
            at_command_logger.error(f"Error writing to serial port for command {command}: {e}")
            raise ModemError(f"Serial write error: {e}") from e

        lines: List[str] = []
        start_time = time.monotonic()
        buffer = bytearray()
        raw_response_bytes = bytearray()

        try:
            while True:
                if time.monotonic() - start_time > actual_timeout:
                    partial_response_str = buffer.decode('utf-8', errors='replace').strip()
                    logger.warning(f"Timeout waiting for response to '{command}'. Buffer: '{partial_response_str}'")
                    at_command_logger.warning(f"Timeout waiting for response to '{command}'. Raw Buffer: {raw_response_bytes.hex(' ')}")
                    at_command_logger.info(f"<-- {partial_response_str}")

                    lines.extend(partial_response_str.splitlines())
                    if expect_ok:
                        raise ModemError(f"Timeout for command: {command}. Response: {lines}")
                    return lines # Return partial if not expecting OK and timed out

                byte = self.port.read(1) # This can raise SerialException on some disconnections
                if not byte:
                    continue # Timeout on read(1) if port.timeout is set, or just empty if not.

                buffer.append(byte[0])
                raw_response_bytes.append(byte[0])

                # Try to decode and check for final response line
                # This part needs to be careful about partial UTF-8 sequences
                decoded_buffer_segment = ""
                try:
                    # Try to decode what we have so far. If it ends in a partial char, this might fail.
                    decoded_buffer_segment = buffer.decode('utf-8')
                except UnicodeDecodeError:
                    # If decode fails, it might be a multi-byte char. Wait for more bytes unless no more are coming.
                    if self.port.in_waiting > 0:
                        continue
                    else: # No more bytes coming, decode with replacement
                        decoded_buffer_segment = buffer.decode('utf-8', errors='replace')
                
                # Normalize line endings and split into potential lines
                # We are looking for \n as the primary line separator after normalization
                processed_buffer_segment = decoded_buffer_segment.replace('\r\n', '\n').replace('\r', '\n')
                
                # Check if the full buffer (not just segment) contains a final response marker
                # This is safer than checking processed_buffer_segment which might be partial
                current_full_buffer_str = buffer.decode('utf-8', errors='replace')
                normalized_full_buffer_str = current_full_buffer_str.replace('\r\n', '\n').replace('\r', '\n')
                
                # Check for standard terminators in the *entire accumulated buffer*
                # This avoids issues with processing line-by-line if terminators are split weirdly
                temp_response_lines = [line.strip() for line in normalized_full_buffer_str.split('\n') if line.strip()]
                
                is_final_response = False
                if temp_response_lines:
                    last_line_candidate = temp_response_lines[-1]
                    if last_line_candidate == "OK" or \
                       last_line_candidate == "ERROR" or \
                       last_line_candidate.startswith("+CME ERROR:") or \
                       last_line_candidate.startswith("+CMS ERROR:") or \
                       (not expect_ok and command.upper().startswith("ATD") and ("CONNECT" in last_line_candidate or "NO CARRIER" in last_line_candidate)) or \
                       (not expect_ok and command.upper().startswith("AT+CGDATA") and "CONNECT" in last_line_candidate.upper()): # Specific for CGDATA
                        is_final_response = True

                if is_final_response:
                    final_response_str = buffer.decode('utf-8', errors='replace').strip()
                    at_command_logger.info(f"<-- {final_response_str}")
                    # at_command_logger.debug(f"<-- RAW_BYTES: {raw_response_bytes.hex(' ')}")

                    lines = [line for line in normalized_full_buffer_str.split('\n') if line.strip()]
                    for line_content in lines:
                        logger.debug(f"<-- {line_content}")
                    
                    is_error = any(
                        line.strip() == "ERROR" or \
                        line.strip().startswith("+CME ERROR:") or \
                        line.strip().startswith("+CMS ERROR:") for line in lines
                    )

                    if expect_ok and is_error:
                        raise ModemError(f"Command '{command}' failed. Response: {lines}")
                    
                    if expect_ok and not any(line.strip() == "OK" for line in lines) and not is_error:
                        # This case might indicate an incomplete or unexpected successful response
                        logger.warning(f"Command '{command}' did not return 'OK' but no explicit error. Response: {lines}")
                        # Depending on strictness, could raise ModemError here too.
                        # For now, if no error and expect_ok, but no OK, it's a bit ambiguous.
                        # If lines is empty and no OK/ERROR, it's also an issue.
                        if not lines:
                             raise ModemError(f"Command '{command}' returned no data and no OK/ERROR. Response: {lines}")


                    return lines
        
        except serial.SerialException as e: # Catch serial I/O errors during read
            logger.error(f"Serial exception during read for '{command}': {e}")
            at_command_logger.error(f"Serial exception during read for '{command}': {e}")
            # Log current buffer content if any
            partial_response_str = buffer.decode('utf-8', errors='replace').strip()
            if partial_response_str:
                logger.debug(f"Partial buffer on serial exception: '{partial_response_str}'")
                at_command_logger.debug(f"Partial buffer on serial exception: '{partial_response_str}'")
            raise ModemError(f"Serial read I/O error for {command}: {e}") from e
        except ModemError: # Re-raise ModemErrors (e.g., from "ERROR" response)
            raise
        except Exception as e: # Catch any other unexpected error
            logger.error(f"Unexpected exception processing response for '{command}': {e}", exc_info=True)
            at_command_logger.error(f"Unexpected exception processing response for '{command}': {e}")
            partial_response_str = buffer.decode('utf-8', errors='replace').strip()
            if partial_response_str:
                 at_command_logger.debug(f"Partial buffer on unexpected exception: '{partial_response_str}'")
            raise ModemError(f"Unexpected error processing response for {command}: {e}") from e
        
        # This part should ideally not be reached if timeout logic is robust
        final_decoded_buffer_fallback = buffer.decode('utf-8', errors='replace').strip()
        logger.warning(f"Read loop for '{command}' exited unexpectedly. Buffer: '{final_decoded_buffer_fallback}'")
        at_command_logger.warning(f"Read loop for '{command}' exited unexpectedly. Raw Buffer: {raw_response_bytes.hex(' ')}")
        at_command_logger.info(f"<-- {final_decoded_buffer_fallback}")
        lines = [line for line in final_decoded_buffer_fallback.splitlines() if line.strip()]
        if expect_ok and not any("OK" in line for line in lines):
             raise ModemError(f"Command '{command}' likely failed or response incomplete (fallback). Response: {lines}")
        return lines


    def is_port_available(self) -> bool:
        if not self.port or not self.port.is_open:
            return False
        try:
            # Check if port is open and DSR is high (if modem supports/uses it)
            # PySerial's dsr attribute might not be universally reliable for USB modems.
            # A more robust check might involve sending a benign AT command like "AT"
            # and expecting "OK", but that's too heavy for a simple availability check.
            # For now, is_open is the primary check. Physical disconnects often invalidate this.
            return self.port.is_open 
        except Exception as e:
            logger.debug(f"Exception checking port availability ({self.port_name}): {e}")
            return False

    def get_modem_port_details(self) -> dict:
        return self.modem_details

    def set_modem_port_details(self, details: dict):
        self.modem_details = details
