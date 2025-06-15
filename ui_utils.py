import sys
import time
import threading
import math
import platform
import os
from typing import Optional

# ANSI escape codes for cursor control and clearing
CURSOR_OFF = "\033[?25l"
CURSOR_ON = "\033[?25h"
CLEAR_LINE = "\033[K"
CURSOR_UP_ONE = "\033[A"

class Spinner:
    def __init__(self, message: str = "Processing...", delay: float = 0.1):
        self.message = message
        self.delay = delay
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.frames = ['-', '\\', '|', '/']
        # For PowerShell like spinner: . .. ... ....
        # self.frames = ['.   ', '..  ', '... ', '....'] 

    def _spin(self):
        frame_idx = 0
        sys.stdout.write(CURSOR_OFF)
        while self._running:
            # For PowerShell like spinner:
            # text = f"{self.message}{self.frames[frame_idx % len(self.frames)]}"
            # sys.stdout.write(f"\r{text}{CLEAR_LINE}")
            
            # Standard spinner
            sys.stdout.write(f"\r{self.message} {self.frames[frame_idx % len(self.frames)]}{CLEAR_LINE}")
            sys.stdout.flush()
            frame_idx += 1
            time.sleep(self.delay)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def stop(self, success: Optional[bool] = None):
        self._running = False
        if self._thread:
            self._thread.join(timeout=self.delay * 2) # Wait for thread to finish
        
        final_message = self.message
        if success is True:
            final_message += " DONE!"
        elif success is False:
            final_message += " ERROR!"
        else: # Neutral stop
            final_message += " stopped."
            
        sys.stdout.write(f"\r{final_message}{CLEAR_LINE}\n")
        sys.stdout.write(CURSOR_ON)
        sys.stdout.flush()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop(success=exc_type is None)


def get_bars(value: Optional[float], min_val: float, max_val: float, bar_width: int = 8) -> str:
    if value is None or value < min_val:
        value = min_val
    if value > max_val:
        value = max_val

    if max_val == min_val: # Avoid division by zero
        bar_fill_count = bar_width if value >= max_val else 0
    else:
        ratio = (value - min_val) / (max_val - min_val)
        bar_fill_count = math.floor(ratio * bar_width)
        bar_fill_count = max(0, min(bar_width, bar_fill_count)) # Clamp

    bar_empty_count = bar_width - bar_fill_count
    
    # Unicode characters from PowerShell script:
    # CHAR_FILL = chr(0x2588)  # Full block '█'
    # CHAR_EMPTY = chr(0x2591) # Light shade '░'
    # Using simpler characters for broader terminal compatibility initially
    char_fill = '█'
    char_empty = '░'
    
    return f"[{char_fill * bar_fill_count}{char_empty * bar_empty_count}]"

def clear_screen():
    if platform.system().lower() == "windows":
        os.system('cls')
    else:
        os.system('clear')

def hide_cursor():
    sys.stdout.write(CURSOR_OFF)
    sys.stdout.flush()

def show_cursor():
    sys.stdout.write(CURSOR_ON)
    sys.stdout.flush()

# Store initial cursor position for status updates (if needed)
# This is more complex than just printing; requires knowing current line
# and moving cursor back. For now, status updates will just print sequentially.
# status_cursor_position = None 
# def save_cursor_position():
#     global status_cursor_position
#     # This would need platform specific way to get cursor pos or use a library like 'blessings'
#     pass 
# def restore_cursor_position():
#     if status_cursor_position:
#         # Move cursor to status_cursor_position
#         pass

def print_at(line: int, col: int, text: str):
    """Prints text at a specific line and column (1-indexed)."""
    sys.stdout.write(f"\033[{line};{col}H{text}")
    sys.stdout.flush()

def clear_lines_from(line: int):
    """Clears all lines from the specified line to the bottom of the screen."""
    # Move to the specified line, first column
    sys.stdout.write(f"\033[{line};1H")
    # Clear from cursor to end of screen
    sys.stdout.write("\033[J")
    sys.stdout.flush()

