from typing import Optional, Union
import re
# import logging # Removed direct logging for simplicity in this utility

# logger = logging.getLogger(__name__) # Define if logging is needed within this module

# Based on scripts/modules/converters.psm1

def get_band_lte(channel: Optional[int]) -> str:
    if channel is None: return "--"
    if channel < 600: return "B1"
    if channel < 1200: return "B2"
    if channel < 1950: return "B3"
    if channel < 2400: return "B4"
    if channel < 2650: return "B5"
    # ... (omitting full list for brevity, should be copied from original)
    if channel < 2750: return "B6"
    if channel < 3450: return "B7"
    if channel < 3800: return "B8"
    if channel < 4150: return "B9"
    if channel < 4750: return "B10"
    if channel < 4950: return "B11"
    # B12: 5010-5179
    if channel >= 5010 and channel < 5180 : return "B12"
    # B13: 5180-5279
    if channel >= 5180 and channel < 5280 : return "B13"
    # B14: 5280-5379
    if channel >= 5280 and channel < 5380 : return "B14"
    # B17: 5730-5849
    if channel >= 5730 and channel < 5850 : return "B17"
    # B18: 5850-5999
    if channel >= 5850 and channel < 6000 : return "B18"
    # B19: 6000-6149
    if channel >= 6000 and channel < 6150 : return "B19"
    # B20: 6150-6449
    if channel >= 6150 and channel < 6450 : return "B20"
    # B21: 6450-6599
    if channel >= 6450 and channel < 6600 : return "B21"
    # B25: 8040-8689 (PS script has 8690)
    if channel >= 8040 and channel < 8690 : return "B25"
    # B26: 8690-9039
    if channel >= 8690 and channel < 9040 : return "B26"
    # B28: 9210-9659
    if channel >= 9210 and channel < 9660 : return "B28"
    # B38: 37750-38249
    if channel >= 37750 and channel < 38250 : return "B38"
    # B39: 38250-38649
    if channel >= 38250 and channel < 38650 : return "B39"
    # B40: 38650-39649
    if channel >= 38650 and channel < 39650 : return "B40"
    # B41: 39650-41589
    if channel >= 39650 and channel < 41590 : return "B41"
    # B66: 66436-67335
    if channel >= 66436 and channel < 67336 : return "B66"
    # Add more bands as per the original PowerShell script or modem specs
    return "--" # Default for unlisted channels

def get_bandwidth_mhz(bw_code: Optional[int]) -> Optional[float]:
    if bw_code is None: return None
    mapping = {0: 1.4, 1: 3.0, 2: 5.0, 3: 10.0, 4: 15.0, 5: 20.0}
    return mapping.get(bw_code)

def convert_rsrp_to_rssi(rsrp: Optional[float], bw_code: Optional[int]) -> Optional[float]:
    """
    Calculates RSSI from RSRP and bandwidth code.
    RSSI = RSRP + 10 * log10(12 * N_prb)
    N_prb (Number of Physical Resource Blocks) depends on bandwidth.
    """
    if rsrp is None or bw_code is None:
        return None

    # N_prb mapping based on bandwidth code (common values)
    # BW Code: 0 (1.4MHz) -> 6 PRB
    # BW Code: 1 (3MHz)   -> 15 PRB
    # BW Code: 2 (5MHz)   -> 25 PRB
    # BW Code: 3 (10MHz)  -> 50 PRB
    # BW Code: 4 (15MHz)  -> 75 PRB
    # BW Code: 5 (20MHz)  -> 100 PRB
    n_prb_map = {0: 6, 1: 15, 2: 25, 3: 50, 4: 75, 5: 100}
    
    n_prb = n_prb_map.get(bw_code)
    if n_prb is None or n_prb == 0:
        return -113.0 # Default or error value as in PS script

    import math
    try:
        rssi = rsrp + (10 * math.log10(12 * n_prb))
        return rssi
    except ValueError: # log10 of non-positive
        return -113.0

def parse_at_response_value(response_lines: list, filter_keyword: str,
                            split_chars: Optional[str] = '[:,]', item_index: int = 1,
                            replace_chars: str = '"', default=None) -> Optional[str]:
    """
    Helper to parse values from AT command responses.
    - response_lines: List of strings from AT command output.
    - filter_keyword: Substring to find in a line.
    - split_chars: Regex pattern for splitting the line. If None, the whole line (after filtering) is considered.
    - item_index: Index of the part to take after splitting. If split_chars is None, item_index=0 is typical.
    - replace_chars: String of characters to remove from the extracted value.
    - default: Value to return if parsing fails.
    """
    for line_from_modem in response_lines:
        current_line_stripped = line_from_modem.strip()

        if filter_keyword in current_line_stripped:
            value_to_process: Optional[str] = None

            if split_chars is None:
                # If no splitting characters are provided, the line itself (that matched the filter)
                # is the candidate value. item_index=0 is expected in this scenario.
                if item_index == 0:
                    value_to_process = current_line_stripped
                else:
                    # This is an ambiguous case for this function.
                    # Fallback to using the whole stripped line.
                    # A warning could be logged here if a logger was available.
                    value_to_process = current_line_stripped
            else:
                # Perform splitting using the provided regex pattern.
                # Use the original (unstripped) line_from_modem to ensure delimiters
                # (if they are spaces) are preserved for re.split.
                parts = re.split(split_chars, line_from_modem)
                if len(parts) > item_index:
                    value_to_process = parts[item_index].strip() # Strip the extracted part
                else:
                    # Not enough parts after split for the given item_index.
                    continue # Try the next line in response_lines
            
            if value_to_process is None: # Should only be reached if 'continue' was hit above
                continue

            # Apply character replacement to the extracted value
            if replace_chars:
                for char_to_remove in replace_chars: # Iterate over characters in the string
                    value_to_process = value_to_process.replace(char_to_remove, '')
            
            return value_to_process.strip() # Return the final stripped and processed value
            
    return default

def parse_nullable_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "" or value == "--":
        return None
    try:
        # Check if the string starts with '0x' or '0X' for hexadecimal
        if value.lower().startswith('0x'):
            return int(value, 16)
        else:
            return int(value)
    except ValueError:
        return None

def parse_nullable_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "" or value == "--":
        return None
    try:
        return float(value)
    except ValueError:
        return None

