import threading
import time
import logging
import platform

# Assuming ModemManager and NetworkManager classes are defined elsewhere
# from modem_manager import ModemManager # Circular dependency if ModemManager uses this
# from network_manager import NetworkManager

logger = logging.getLogger(__name__)

class SerialPortMonitor(threading.Thread):
    def __init__(self, modem_manager_instance, check_interval=5):
        super().__init__(daemon=True, name="SerialPortMonitor")
        self.modem_manager = modem_manager_instance # Pass instance
        self.check_interval = check_interval
        self._stop_event = threading.Event()
        self.disconnected_event = threading.Event() # External event to signal disconnection

    def run(self):
        logger.info("Serial port monitor started.")
        initial_port_name = self.modem_manager.port_name
        if not initial_port_name:
            logger.warning("Serial port monitor started but no port was initially open.")
            # Potentially wait for a port to be opened or exit
            # For now, it will just loop if port is not set.

        while not self._stop_event.is_set():
            if not self.modem_manager.port_name: # Port was never opened or closed
                if initial_port_name: # It was open before, now it's gone
                    logger.warning(f"Monitored serial port {initial_port_name} is no longer set in modem_manager.")
                    self.disconnected_event.set()
                    break # Exit thread as the port context is lost
                
                if self._stop_event.wait(timeout=self.check_interval):
                    break # Stop event was set during sleep
                continue

            if not self.modem_manager.is_port_available():
                logger.warning(f"Serial port {self.modem_manager.port_name} disconnected or not available.")
                self.disconnected_event.set() # Signal external listeners
                break # Exit thread on disconnect
            
            if self._stop_event.wait(timeout=self.check_interval):
                break # Stop event was set during sleep
        logger.info("Serial port monitor stopped.")

    def stop(self):
        logger.info("Stopping serial port monitor...")
        self._stop_event.set()


class NetworkMonitor(threading.Thread):
    def __init__(self, network_manager_instance, check_interval=10, initial_delay=5):
        super().__init__(daemon=True, name="NetworkMonitor")
        self.network_manager = network_manager_instance # Pass instance
        self.check_interval = check_interval
        self.initial_delay = initial_delay
        self._stop_event = threading.Event()
        self.disconnected_event = threading.Event() # External event

    def run(self):
        logger.info(f"Network monitor started. Initial check delay: {self.initial_delay}s.")

        if self.initial_delay > 0:
            # Wait for the initial delay, but allow early exit if stop_event is set
            if self._stop_event.wait(timeout=self.initial_delay): # wait returns True if event is set
                logger.info("Network monitor stopped during initial delay.")
                return # Exited because stop_event was set

        if not self.network_manager.interface_name:
            logger.warning("Network monitor: No interface is being managed. Exiting monitor.")
            return

        # Perform initial check after delay (if any)
        if not self._stop_event.is_set(): # Check if not stopped during/after delay
            if not self.network_manager.is_connected():
                logger.warning(f"Network interface {self.network_manager.interface_name} reports disconnected (initial check).")
                self.disconnected_event.set()
                logger.info("Network monitor stopped after initial check failure.")
                return # Exit after first check failure
        elif self._stop_event.is_set(): # If stop was called during the delay
             logger.info("Network monitor stopped before initial check could occur.")
             return


        # Main monitoring loop
        while not self._stop_event.is_set():
            # Wait for check_interval or until stop_event is set
            # Perform the check *before* sleeping for the next interval
            if self._stop_event.is_set(): # Check before potentially long operation
                break

            if not self.network_manager.is_connected():
                logger.warning(f"Network interface {self.network_manager.interface_name} reports disconnected. Will keep monitoring without reconnecting.")
            else:
                logger.debug(f"Network interface {self.network_manager.interface_name} is connected.")
            
            # Wait for the next check interval or if stop event is signaled
            if self._stop_event.wait(timeout=self.check_interval):
                break # Stop event was set during sleep
            
        logger.info("Network monitor stopped.")

    def stop(self):
        logger.info("Stopping network monitor...")
        self._stop_event.set()
