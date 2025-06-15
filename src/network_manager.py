import subprocess
import platform
import logging
import re
import time # Added for sleep
from typing import List, Optional, Dict, Tuple

from config import MODEM_MAC_ADDRESS

logger = logging.getLogger(__name__)

class NetworkError(Exception):
    pass

class NetworkManager:
    def __init__(self):
        self.os_type = platform.system().lower()
        self.interface_name: Optional[str] = None # e.g., en0, eth0
        self.macos_service_name: Optional[str] = None # macOS specific service name, e.g., "Wi-Fi"
        self.interface_index: Optional[int] = None # Index might be specific to OS or context
        self.configured_ip_address: Optional[str] = None # Store the IP configured on the interface

    def _run_command(self, command: List[str]) -> Tuple[str, str, int]:
        logger.debug(f"Executing command: {' '.join(command)}")
        try:
            # Note: For commands requiring sudo, this script must be run with sudo.
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            stdout, stderr = process.communicate(timeout=30)
            logger.debug(f"Stdout: {stdout.strip()}")
            if stderr.strip():
                logger.debug(f"Stderr: {stderr.strip()}")
            return stdout, stderr, process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            # Try to get output even after timeout
            stdout, stderr = process.communicate()
            logger.error(f"Command timed out: {' '.join(command)}")
            logger.debug(f"Timeout Stdout: {stdout.strip() if stdout else ''}")
            logger.debug(f"Timeout Stderr: {stderr.strip() if stderr else ''}")
            return stdout if stdout else "", stderr if stderr else "", -1 # Indicate timeout
        except Exception as e:
            logger.error(f"Command failed: {' '.join(command)}, Error: {e}")
            return "", str(e), -2 # Indicate other error

    def _get_macos_network_service_name(self, device_name: str) -> Optional[str]:
        """
        For macOS, finds the "Hardware Port" name (often used as service name)
        for a given device interface name (e.g., "en5").
        """
        if self.os_type != "darwin":
            return None
        
        logger.debug(f"Attempting to find macOS network service name for device: {device_name}")
        stdout, stderr, retcode = self._run_command(["networksetup", "-listallhardwareports"])

        if retcode != 0:
            logger.error(f"Failed to list hardware ports for macOS. Stderr: {stderr}")
            return None

        current_hardware_port = None
        lines = stdout.splitlines()
        for i in range(len(lines)):
            line = lines[i]
            hw_port_match = re.match(r"Hardware Port:\s*(.+)", line)
            if hw_port_match:
                current_hardware_port = hw_port_match.group(1).strip()
            
            dev_match = re.match(r"Device:\s*(.+)", line)
            if dev_match and current_hardware_port:
                found_device = dev_match.group(1).strip()
                if found_device == device_name:
                    # Ethernet Address is usually the next line, can be used for verification if needed
                    logger.info(f"Found macOS Hardware Port (service name): '{current_hardware_port}' for device '{device_name}'")
                    return current_hardware_port
        
        logger.warning(f"Could not find macOS Hardware Port (service name) for device: {device_name}")
        return None

    def find_network_interface_by_mac(self, mac_address: str) -> Optional[str]:
        logger.info(f"Attempting to find network interface with MAC: {mac_address} for OS: {self.os_type}")
        normalized_mac = mac_address.lower().replace('-', ':')

        if self.os_type == "linux":
            try:
                for iface_candidate in subprocess.check_output(['ls', '/sys/class/net/'], text=True).splitlines():
                    iface_candidate = iface_candidate.strip()
                    try:
                        with open(f'/sys/class/net/{iface_candidate}/address', 'r') as f:
                            current_mac = f.read().strip().lower()
                        if current_mac == normalized_mac:
                            logger.info(f"Found Linux interface: {iface_candidate} for MAC: {mac_address}")
                            self.interface_name = iface_candidate
                            return iface_candidate
                    except FileNotFoundError:
                        continue
                    except Exception as e:
                        logger.warning(f"Could not read MAC for {iface_candidate} on Linux: {e}")
            except Exception as e:
                logger.error(f"Error listing network interfaces on Linux: {e}")
            logger.warning(f"Could not find Linux interface for MAC: {mac_address}")
            return None

        elif self.os_type == "darwin": # macOS
            # Use `networksetup -listallhardwareports` which is more structured
            stdout, stderr, retcode = self._run_command(["networksetup", "-listallhardwareports"])
            if retcode != 0:
                logger.error(f"Failed to execute 'networksetup -listallhardwareports'. Stderr: {stderr}")
                return None

            current_device_name = None
            lines = stdout.splitlines()
            for i in range(len(lines)):
                line = lines[i]
                # Order is typically: Hardware Port, Device, Ethernet Address
                dev_match = re.match(r"Device:\s*(.+)", line)
                if dev_match:
                    current_device_name = dev_match.group(1).strip()
                
                mac_match = re.match(r"Ethernet Address:\s*([0-9a-fA-F:]{17})", line)
                if mac_match and current_device_name:
                    found_mac = mac_match.group(1).lower()
                    if found_mac == normalized_mac:
                        logger.info(f"Found macOS interface (Device): {current_device_name} for MAC: {mac_address}")
                        self.interface_name = current_device_name
                        # Also try to get and store the service name
                        self.macos_service_name = self._get_macos_network_service_name(current_device_name)
                        if not self.macos_service_name:
                             logger.warning(f"Found device {current_device_name} but could not determine its service name.")
                        return current_device_name
            
            logger.warning(f"Could not find macOS interface for MAC: {mac_address} using networksetup.")
            # Fallback to ifconfig if networksetup fails to find it (less reliable parsing)
            logger.info("Trying fallback with ifconfig for macOS MAC search...")
            stdout_ifc, _, retcode_ifc = self._run_command(["ifconfig"])
            if retcode_ifc == 0:
                current_iface = None
                for line_ifc in stdout_ifc.splitlines():
                    iface_match = re.match(r'^([a-zA-Z0-9]+): flags=', line_ifc)
                    if iface_match:
                        current_iface = iface_match.group(1)
                    
                    mac_line_match = re.search(r'ether\s+([0-9a-fA-F:]{17})', line_ifc)
                    if mac_line_match and current_iface:
                        current_mac_ifc = mac_line_match.group(1).lower()
                        if current_mac_ifc == normalized_mac:
                            logger.info(f"Found macOS interface (via ifconfig): {current_iface} for MAC: {mac_address}")
                            self.interface_name = current_iface
                            self.macos_service_name = self._get_macos_network_service_name(current_iface)
                            if not self.macos_service_name:
                                logger.warning(f"Found device {current_iface} (via ifconfig) but could not determine its service name.")
                            return current_iface
            logger.warning(f"Could not find macOS interface for MAC: {mac_address} (ifconfig fallback also failed).")
            return None
        else:
            logger.error(f"Unsupported OS for network management: {self.os_type}")
            return None
        return None


    def initialize_network(self, ip_address: str, ip_mask: str, ip_gateway: str, ip_dns: List[str]) -> bool:
        if not self.interface_name:
            logger.error("Interface name not set. Cannot initialize network.")
            return False

        logger.info(f"Initializing network for interface: {self.interface_name} on {self.os_type}")
        logger.info(f"IP: {ip_address}, Mask: {ip_mask}, GW: {ip_gateway}, DNS: {ip_dns}")
        logger.warning("This operation usually requires administrator (sudo/root) privileges.")
        self.configured_ip_address = None # Reset before attempting configuration

        if self.os_type == "linux":
            # Requires root privileges.
            # Example: prefix_length = sum([bin(int(x)).count('1') for x in ip_mask.split('.')])
            # sudo ip link set dev {self.interface_name} up
            # sudo ip addr flush dev {self.interface_name}
            # sudo ip addr add {ip_address}/{prefix_length} dev {self.interface_name}
            # sudo ip route add default via {ip_gateway} dev {self.interface_name}
            # sudo resolvectl dns {self.interface_name} {dns_server1} {dns_server2}
            logger.warning("Linux network initialization: Placeholder. Requires manual `ip` commands with sudo or enhanced implementation.")
            # For a real implementation, run commands and check return codes.
            # Example:
            # _, _, retcode = self._run_command(["sudo", "ip", "link", "set", self.interface_name, "up"])
            # if retcode != 0: return False
            # self.configured_ip_address = ip_address # Store on success
            return True # Placeholder

        elif self.os_type == "darwin": # macOS
            # Get service name if needed for DNS setting
            if not self.macos_service_name:
                self.macos_service_name = self._get_macos_network_service_name(self.interface_name)
            
            # Use ifconfig for direct IP assignment without routing side-effects
            logger.info(f"Configuring IP for {self.interface_name} using ifconfig...")
            cmd_set_ip = ["ifconfig", self.interface_name, ip_address, "netmask", ip_mask]
            stdout_ip, stderr_ip, retcode_ip = self._run_command(cmd_set_ip)
            if retcode_ip != 0:
                logger.error(f"Failed to set IP configuration for '{self.interface_name}' using ifconfig. Stderr: {stderr_ip}")
                if "must be root" in stderr_ip or "Operation not permitted" in stderr_ip:
                    logger.error("This command requires administrator (sudo) privileges to run.")
                return False
            logger.info(f"Successfully set IP for '{self.interface_name}'.")
            self.configured_ip_address = ip_address

            # Add a short delay to allow the IP configuration to settle
            logger.debug("Waiting for 2 seconds for IP configuration to apply...")
            time.sleep(2)

            # Set DNS using networksetup, which requires the service name
            if self.macos_service_name:
                service_name = self.macos_service_name
                logger.info(f"Configuring DNS for service '{service_name}'...")
                dns_servers_to_set = ip_dns if ip_dns else ["empty"]
                cmd_set_dns = ["networksetup", "-setdnsservers", service_name] + dns_servers_to_set
                stdout_dns, stderr_dns, retcode_dns = self._run_command(cmd_set_dns)
                if retcode_dns != 0:
                    logger.error(f"Failed to set DNS servers for '{service_name}'. Stderr: {stderr_dns}")
                    # This is not a fatal error for routing, so we can continue but log a warning.
                else:
                    logger.info(f"Successfully set DNS for '{service_name}'.")
            else:
                logger.warning(f"Could not determine macOS service name for {self.interface_name}. Skipping DNS configuration.")

            # Explicitly manage the default route
            # First, try to delete any existing default route to avoid conflicts.
            logger.info("Attempting to delete existing default route...")
            cmd_del_route = ["route", "delete", "default"]
            stdout_del, stderr_del, retcode_del = self._run_command(cmd_del_route)
            if retcode_del == 0:
                logger.info("Successfully deleted existing default route.")
            else:
                # This will often fail if no default route exists, which is fine.
                logger.info(f"Could not delete default route (it may not have existed). Stderr: {stderr_del.strip()}")

            # Now, add the new default route via our interface
            logger.info(f"Attempting to add default route via interface {self.interface_name}...")
            cmd_add_route = ["route", "add", "-net", "default", "-interface", self.interface_name]
            
            stdout_route, stderr_route, retcode_route = self._run_command(cmd_add_route)
            if retcode_route == 0:
                logger.info(f"Successfully added default route via {self.interface_name}.")
            else:
                logger.error(f"Failed to add default route for {self.interface_name}. Stderr: {stderr_route.strip()}")
                if "must be root" in stderr_route or "Operation not permitted" in stderr_route:
                    logger.error("This command requires administrator (sudo) privileges to run.")
                return False
            
            return True
        else:
            logger.error(f"Network initialization not supported for OS: {self.os_type}")
            return False

    def is_connected(self) -> bool:
        if not self.interface_name:
            logger.debug("is_connected: No interface name set, returning False.")
            return False

        logger.debug(f"Checking connectivity for interface {self.interface_name} on {self.os_type}")
        
        if self.os_type == "linux":
            stdout, _, retcode = self._run_command(["ip", "addr", "show", "dev", self.interface_name])
            if retcode != 0 or not ("inet " in stdout and "UP" in stdout.upper()):
                logger.debug(f"Interface {self.interface_name} not up or no IP on Linux.")
                return False
        elif self.os_type == "darwin":
            stdout_ifc, stderr_ifc, retcode_ifc = self._run_command(["ifconfig", self.interface_name])
            if retcode_ifc != 0:
                logger.debug(f"ifconfig command for {self.interface_name} failed with retcode {retcode_ifc}. Stderr: {stderr_ifc.strip()}")
                return False
            is_flag_up = False
            flags_match = re.search(r"flags=\d+<([^>]+)>", stdout_ifc)
            if flags_match:
                if "UP" in flags_match.group(1).split(','):
                    is_flag_up = True
            has_inet = "inet " in stdout_ifc
            if not (is_flag_up and has_inet):
                logger.debug(f"Interface {self.interface_name} on macOS is not UP or does not have an IP. UP flag found: {is_flag_up}, Has inet line: {has_inet}.")
                return False
            logger.debug(f"Interface {self.interface_name} on macOS is UP and has an IP. Proceeding to ping test.")
        
        # Generic ping test to a reliable public DNS server
        ping_target = "8.8.8.8"
        ping_command_base = ["ping", "-c", "1"] # -c 1 for one packet

        if self.os_type == "linux":
            ping_command = ping_command_base + ["-W", "1", ping_target] # -W 1 for 1 second timeout
            # Optionally bind to interface: ping_command.extend(["-I", self.interface_name])
            # Or bind to source IP if known:
            # if self.configured_ip_address:
            #    ping_command.extend(["-I", self.configured_ip_address]) # -I can take IP or iface name
        elif self.os_type == "darwin":
            ping_command = ping_command_base + ["-t", "1", ping_target] # -t 1 for 1 second timeout
            if self.configured_ip_address:
                # macOS ping uses -S to specify source address
                ping_command = ["ping", "-S", self.configured_ip_address, "-c", "1", "-t", "1", ping_target]
                logger.debug(f"Using source-specific ping for macOS: {' '.join(ping_command)}")
            else:
                 logger.debug(f"Using generic ping for macOS (no source IP stored): {' '.join(ping_command)}")
        else: # Windows or other (basic)
            ping_command = ["ping", "-n", "1", "-w", "1000", ping_target] # -n 1 count, -w 1000ms timeout

        _, stderr_ping, retcode_ping = self._run_command(ping_command)
        
        if retcode_ping == 0:
            logger.debug(f"Ping test to {ping_target} successful.")
            return True
        else:
            logger.debug(f"Ping test to {ping_target} failed. Retcode: {retcode_ping}, Stderr: {stderr_ping.strip()}")
            if "No route to host" in stderr_ping:
                logger.warning(f"Ping failed: No route to host from {self.configured_ip_address if self.configured_ip_address else 'system'} to {ping_target} via {self.interface_name}.")
            elif "timeout" in stderr_ping.lower() or retcode_ping == -1 : # -1 was our timeout indicator
                logger.warning(f"Ping failed: Request timed out for {ping_target} via {self.interface_name}.")
            # Other errors could be "unknown host" if DNS is broken, but "No route" is more fundamental.
            return False
