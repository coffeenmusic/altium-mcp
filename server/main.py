from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.utilities.types import Image as MCPImage
import json
import os
import time
import asyncio
import logging
import subprocess
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import Dict, Any, Optional
import sys
import win32gui
import win32ui
import win32con
import win32api
import win32process
from PIL import Image
import io
import base64
import glob
import re
import silkscreen
import label_blocks
import script_bundle
import pcblib_file

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG for more detailed logs
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Output to console
        logging.FileHandler(str(Path(__file__).with_name('altium_mcp.log')))  # Also log to file
    ]
)
logger = logging.getLogger("AltiumMCPServer")

# Set MCP_DIR to the directory of the current Python file
MCP_DIR = Path(__file__).parent
# config.json lives in a per-user folder OUTSIDE the install directory and
# outside AppData. Claude Desktop replaces the extension folder on every
# update, which used to reset a hand-picked Altium path; and being an MSIX
# package it redirects its child processes' AppData writes into its own
# LocalCache. start_server.py passes the folder it chose in ALTIUM_MCP_HOME.
# LEGACY_CONFIG_FILE is read once to migrate.
def _data_dir() -> Path:
    override = os.environ.get("ALTIUM_MCP_HOME")
    return Path(override) if override else Path.home() / ".altium-mcp"

LEGACY_CONFIG_FILE = MCP_DIR / "config.json"
CONFIG_FILE = _data_dir() / "config.json"
DEFAULT_SCRIPT_PATH = MCP_DIR / "AltiumScript" / "Altium_API.PrjScr"

# Use a fixed exchange directory for request/response JSON files.
# Both the Python MCP server and the Altium DelphiScript need to independently
# resolve to the same directory. C:\Users\Public is writable by all users and
# exists on every Windows machine. This avoids fragile script-project-path
# resolution that breaks when Altium caches stale script projects.
EXCHANGE_DIR = Path("C:/Users/Public/altium_mcp")
EXCHANGE_DIR.mkdir(exist_ok=True)
REQUEST_FILE = EXCHANGE_DIR / "request.json"
RESPONSE_FILE = EXCHANGE_DIR / "response.json"

# Initialize FastMCP server
mcp = FastMCP("AltiumMCP", description="Altium integration through the Model Context Protocol")

class AltiumConfig:
    def __init__(self):
        self.altium_exe_path = ""
        self.script_path = str(DEFAULT_SCRIPT_PATH)
        self.load_config()
    
    def load_config(self):
        """Load configuration, migrating a pre-relocation config.json once."""
        source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
        if source.exists():
            try:
                with open(source, "r") as f:
                    config = json.load(f)
                self.altium_exe_path = config.get("altium_exe_path", "")
                # Only a hand-picked script project is persisted. A saved path
                # that no longer exists (old install folder) falls back to the
                # project shipped beside this file instead of prompting.
                saved_script = config.get("script_path", "")
                if saved_script and os.path.exists(saved_script):
                    self.script_path = saved_script
                logger.info(f"Loaded configuration from {source}")
                if source is LEGACY_CONFIG_FILE:
                    self.save_config()
                else:
                    self._saved = self._as_dict()
            except Exception as e:
                logger.error(f"Error loading configuration: {e}")
                self._create_default_config()
        else:
            logger.info("No configuration file found, creating default")
            self._create_default_config()

    def _create_default_config(self):
        """Create a default configuration file with improved Altium executable discovery"""
        
        # Try to find Altium directories dynamically
        altium_base_path = r"C:\Program Files\Altium"
        altium_exe_path = None
        
        if os.path.exists(altium_base_path):
            # Find all directories that match the pattern AD*
            ad_dirs = glob.glob(os.path.join(altium_base_path, "AD*"))
            
            if ad_dirs:
                # Sort directories by version number (extract the number after "AD")
                def get_version_number(dir_path):
                    match = re.search(r"AD(\d+)", os.path.basename(dir_path))
                    if match:
                        return int(match.group(1))
                    return 0
                
                # Sort directories by version number (highest first)
                ad_dirs.sort(key=get_version_number, reverse=True)
                
                # Try each directory until we find one with X2.EXE
                for ad_dir in ad_dirs:
                    potential_exe = os.path.join(ad_dir, "X2.EXE")
                    if os.path.exists(potential_exe):
                        altium_exe_path = potential_exe
                        break
        
        # Set the found path (or empty string if nothing found)
        self.altium_exe_path = altium_exe_path if altium_exe_path else ""
        
        # Save the configuration
        self.save_config()
    
    def _as_dict(self):
        config = {"altium_exe_path": self.altium_exe_path}
        # The default script project belongs to whichever install is running
        # (extension folder or a dev checkout), so it is never written down:
        # both share this file and must not steal each other's scripts.
        if Path(self.script_path) != DEFAULT_SCRIPT_PATH:
            config["script_path"] = self.script_path
        return config

    def save_config(self):
        """Save configuration to file, only when something changed"""
        config = self._as_dict()
        if config == getattr(self, "_saved", None) and CONFIG_FILE.exists():
            return

        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            self._saved = config
            logger.info(f"Saved configuration to {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def verify_paths(self):
        """Verify that the paths in the configuration exist, prompt for input if they don't"""

        # Initialize variables
        root = None
        paths_verified = True
        
        # Check Altium executable
        if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
            paths_verified = False
            
            # Before prompting, try an automatic discovery
            altium_base_path = r"C:\Program Files\Altium"
            if os.path.exists(altium_base_path):
                logger.info(f"Attempting automatic discovery in {altium_base_path}")
                # Find all directories that match the pattern AD*
                ad_dirs = glob.glob(os.path.join(altium_base_path, "AD*"))
                
                if ad_dirs:
                    # Sort directories by version number (extract the number after "AD")
                    def get_version_number(dir_path):
                        match = re.search(r"AD(\d+)", os.path.basename(dir_path))
                        if match:
                            return int(match.group(1))
                        return 0
                    
                    # Sort directories by version number (highest first)
                    ad_dirs.sort(key=get_version_number, reverse=True)
                    
                    # Try each directory until we find one with X2.EXE
                    for ad_dir in ad_dirs:
                        potential_exe = os.path.join(ad_dir, "X2.EXE")
                        if os.path.exists(potential_exe):
                            self.altium_exe_path = potential_exe
                            logger.info(f"Automatically found Altium at: {self.altium_exe_path}")
                            print(f"Automatically found Altium at: {self.altium_exe_path}")
                            paths_verified = True
                            break
            
            # If automatic discovery failed, prompt for input
            if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
                if root is None:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()  # Hide the main window
                
                logger.info("Altium executable not found. Prompting user for selection...")
                print(f"Altium executable not found. Searched in:")
                print(f"  - Automatically scanned C:\\Program Files\\Altium\\AD*\\X2.EXE")
                print(f"  - Last known path: {self.altium_exe_path}")
                print("Please select the Altium X2.EXE file...")
                
                self.altium_exe_path = filedialog.askopenfilename(
                    title="Select Altium Executable",
                    filetypes=[("Executable files", "*.exe")],  # Only allow .exe files
                    initialdir="C:/Program Files/Altium"
                )
                
                if not self.altium_exe_path:
                    logger.error("No Altium executable selected. Some functionality may not work.")
                    print("Warning: No Altium executable selected. Automatic script execution will be disabled.")
                    paths_verified = False
        
        # Check script path
        if not os.path.exists(self.script_path):
            paths_verified = False
            
            if root is None:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()  # Hide the main window
            
            logger.info(f"Script file not found at {self.script_path}. Prompting user for selection...")
            print(f"Script file not found at {self.script_path}. Please select the Altium project file...")
            
            selected_path = filedialog.askopenfilename(
                title="Select Altium Project File",
                filetypes=[("Altium Project files", "*.PrjScr")],  # Changed to PrjScr for script project
                initialdir=str(MCP_DIR)
            )
            
            if selected_path:
                self.script_path = selected_path
            else:
                logger.error("No script file selected. Some functionality may not work.")
                print("Warning: No script file selected. Please make sure to create one.")
                paths_verified = False
        
        # Clean up tkinter root if created
        if root is not None:
            root.destroy()
        
        # Save the updated configuration
        self.save_config()
        
        return paths_verified

# Two Altium resources run out over a long session, and at either limit
# Altium fails and then crashes, losing unsaved work:
# - USER objects (windows, menus), capped per process at 10,000 by default.
#   Running a script PROJECT leaked about 105 per call (hidden copies of the
#   forms of the user's menu scripts); the bridge now runs a script file,
#   but the user's own menu scripts still leak.
# - Memory: Altium's script engine leaks a little on every property read of
#   a board object, about 25 MB per silkscreen export on a 40k-object board
#   and 50 MB per full silkscreen check, and never gives it back.
# Calls stop while there is still room to save.
USER_OBJECT_RESERVE = 1000
MEMORY_SHARE_LIMIT = 0.5        # of physical RAM

def altium_resources() -> Optional[dict]:
    """USER objects, their quota, private memory and the machine's RAM (MB)
    for the running Altium, or None when it is not running."""
    import ctypes
    from ctypes import wintypes

    class MemCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]

    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows") as key:
            quota = int(winreg.QueryValueEx(key, "USERProcessHandleQuota")[0])
    except OSError:
        quota = 10000
    kernel32, user32, psapi = ctypes.windll.kernel32, ctypes.windll.user32, ctypes.windll.psapi
    kernel32.OpenProcess.restype = wintypes.HANDLE
    status = MemStatus()
    status.dwLength = ctypes.sizeof(MemStatus)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    found = None
    for pid in win32process.EnumProcesses():
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            name = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if (kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))
                    and os.path.basename(name.value).upper() == "X2.EXE"):
                counters = MemCounters()
                counters.cb = ctypes.sizeof(MemCounters)
                psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
                # Launcher instances are short-lived; the main one is the biggest
                here = {"user_objects": user32.GetGuiResources(handle, 1),     # GR_USEROBJECTS
                        "private_mb": counters.PrivateUsage / 2 ** 20}
                if found is None or here["private_mb"] > found["private_mb"]:
                    found = here
        finally:
            kernel32.CloseHandle(handle)
    if found is not None:
        found.update(quota=quota, ram_mb=status.ullTotalPhys / 2 ** 20)
    return found

def altium_limit_problem() -> Optional[str]:
    """Why calling into Altium now would risk a crash, or None."""
    r = altium_resources()
    if r is None:
        return None
    if r["user_objects"] > r["quota"] - USER_OBJECT_RESERVE:
        return (f"Altium is using {r['user_objects']} of its {r['quota']} window handles and will fail, "
                "then crash, near the limit. Ask the user to save their work in Altium and restart it.")
    if r["private_mb"] > MEMORY_SHARE_LIMIT * r["ram_mb"]:
        return (f"Altium is using {r['private_mb'] / 1024:.1f} GB of memory ({r['ram_mb'] / 1024:.0f} GB in the "
                "machine); its script engine leaks memory on every call and it will run out. Ask the user "
                "to save their work in Altium and restart it.")
    return None

class AltiumBridge:
    def __init__(self):
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)

        # Load configuration
        self.config = AltiumConfig()
        self.config.verify_paths()

        # Commands share a single request.json/response.json pair, so
        # concurrent tool calls must be serialized or they clobber each other
        self._command_lock = asyncio.Lock()

    async def execute_command(self, command: str, params: Dict[str, Any], timeout: float = 120) -> Dict[str, Any]:
        """Execute a command in Altium via the bridge script"""
        async with self._command_lock:
            return await self._execute_command_locked(command, params, timeout)

    async def _execute_command_locked(self, command: str, params: Dict[str, Any], timeout: float = 120) -> Dict[str, Any]:
        try:
            problem = altium_limit_problem()
            if problem:
                return {"success": False, "error": f"Not running '{command}': {problem}"}

            # Clean up any existing response file
            if RESPONSE_FILE.exists():
                RESPONSE_FILE.unlink()
            
            # Write the request file with command and parameters
            with open(REQUEST_FILE, "w") as f:
                json.dump({
                    "command": command,
                    **params  # Include parameters directly in the main JSON object
                }, f, indent=2)
            
            logger.info(f"Wrote request file for command: {command}")
            
            # Run the Altium script
            success = await self.run_altium_script()
            if not success:
                return {"success": False, "error": "Failed to run Altium script"}
            
            # Wait for the response file
            logger.info(f"Waiting for response file to appear...")
            start_time = time.time()
            while not RESPONSE_FILE.exists() and time.time() - start_time < timeout:
                await asyncio.sleep(0.5)
            
            if not RESPONSE_FILE.exists():
                logger.error("Timeout waiting for response from Altium")
                return {"success": False, "error": "No response received from Altium (timeout)"}
            
            # Read the response file and print it for debugging
            logger.info("Response file found, reading response")
            response_text = ""
            with open(RESPONSE_FILE, "r") as f:
                response_text = f.read()
            
            # Log the raw response for debugging
            logger.info(f"Raw response (first 200 chars): {response_text[:200]}")
            
            # Parse the JSON response with detailed error handling
            try:
                response = json.loads(response_text)
                logger.info(f"Successfully parsed JSON response")
                return response
            except json.JSONDecodeError as e:
                logger.error(f"Error parsing JSON response: {e}")
                logger.error(f"Error at position {e.pos}, line {e.lineno}, column {e.colno}")
                logger.error(f"Character at error position: '{response_text[e.pos:e.pos+10]}...'")
                
                # Try to manually fix common JSON issues
                logger.info("Attempting to fix JSON response...")
                fixed_text = response_text
                
                # Fix 1: If there's a quoted JSON array, try to fix it
                if '"[' in fixed_text and ']"' in fixed_text:
                    fixed_text = fixed_text.replace('"[', '[').replace(']"', ']')
                    logger.info("Fixed double-quoted JSON array")
                
                # Fix 2: Handle escaped quotes in JSON strings
                fixed_text = fixed_text.replace('\\"', '"')
                
                # Try to parse the fixed JSON
                try:
                    fixed_response = json.loads(fixed_text)
                    logger.info("Successfully parsed fixed JSON response")
                    return fixed_response
                except json.JSONDecodeError as e2:
                    logger.error(f"Still failed to parse JSON after fixes: {e2}")
                
                # If all else fails, return a structured error
                return {
                    "success": False, 
                    "error": f"Invalid JSON response: {e}",
                    "raw_response": response_text[:500]  # Include part of the raw response for diagnosis
                }
        
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def _resolve_msix_path(virtual_path: str) -> str:
        """Resolve an MSIX-virtualized path to the real filesystem path.

        When Claude Desktop is installed via MSIX (the standard .exe installer
        on modern Windows), file paths are virtualized under AppData\\Roaming\\
        but the real files live at AppData\\Local\\Packages\\Claude_*\\
        LocalCache\\Roaming\\. Child processes of the MSIX app (like Python)
        can see the virtualized paths, but external apps (like Altium) cannot.
        This resolves the path so external processes can find the files.
        """
        appdata = os.environ.get('APPDATA', '')
        if not appdata or not virtual_path.startswith(appdata):
            return virtual_path

        localappdata = os.environ.get('LOCALAPPDATA', '')
        packages_dir = os.path.join(localappdata, 'Packages')
        if not os.path.isdir(packages_dir):
            return virtual_path

        try:
            for item in os.listdir(packages_dir):
                if item.startswith('Claude_'):
                    relative = os.path.relpath(virtual_path, appdata)
                    real_path = os.path.join(packages_dir, item, 'LocalCache', 'Roaming', relative)
                    if os.path.exists(real_path):
                        logger.info(f"Resolved MSIX path: {virtual_path} -> {real_path}")
                        return real_path
        except Exception as e:
            logger.warning(f"Error resolving MSIX path: {e}")

        return virtual_path

    async def run_altium_script(self) -> bool:
        """Run the Altium bridge script"""
        if not os.path.exists(self.config.altium_exe_path):
            logger.error(f"Altium executable not found at: {self.config.altium_exe_path}")
            print(f"Error: Altium executable not found. Please check the configuration.")
            return False

        if not os.path.exists(self.config.script_path):
            logger.error(f"Script file not found at: {self.config.script_path}")
            print(f"Error: Script file not found. Please check the configuration.")
            return False

        try:
            # Resolve MSIX-virtualized path so Altium (an external process
            # outside the MSIX sandbox) can find the script files
            script_path = self._resolve_msix_path(self.config.script_path)

            # Run the units merged into one script FILE: running the script
            # project leaks window handles in Altium (see script_bundle.py).
            # The project stays the source, and the fallback.
            try:
                bundle = script_bundle.ensure_bundle(script_path, EXCHANGE_DIR / "Altium_API_bundle.pas")
                command = (f'"{self.config.altium_exe_path}" -RScriptingSystem:RunScriptFile('
                           f'FileName={bundle}^|ProcName=Run)')
            except (OSError, ValueError) as e:
                logger.warning(f"Could not build the script bundle ({e}); running the project")
                command = (f'"{self.config.altium_exe_path}" -RScriptingSystem:RunScript('
                           f'ProjectName="{script_path}"^|ProcName="Altium_API>Run")')
            
            logger.info(f"Running command: {command}")
            
            # Start the process
            process = subprocess.Popen(command, shell=True)
            
            # Don't wait for completion - Altium will run the script and generate the response
            logger.info(f"Launched Altium with script, process ID: {process.pid}")
            return True
        
        except Exception as e:
            logger.error(f"Error launching Altium: {e}")
            return False

# Create a global bridge instance
altium_bridge = AltiumBridge()

@mcp.tool()
async def get_all_component_property_names(ctx: Context) -> str:
    """
    Get all available component property names (JSON keys) from all components
    
    Returns:
        str: JSON array with all unique property names
    """
    logger.info("Getting all component property names")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "get_all_component_data", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", [])
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
            
        # Extract all unique property names from all components
        property_names = set()
        for component in components_list:
            property_names.update(component.keys())
        
        # Convert set to sorted list for consistent output
        property_list = sorted(list(property_names))
        
        logger.info(f"Found {len(property_list)} unique property names")
        return json.dumps(property_list, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_component_property_values(ctx: Context, property_name: str) -> str:
    """
    Get values of a specific property for all components
    
    Args:
        property_name (str): The name of the property to get values for
    
    Returns:
        str: JSON array with objects containing designator and property value
    """
    logger.info(f"Getting values for property: {property_name}")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "get_all_component_data", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", [])
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
            
        # Extract the property values along with designators
        property_values = []
        for component in components_list:
            designator = component.get("designator")
            if designator and property_name in component:
                property_values.append({
                    "designator": designator,
                    "value": component.get(property_name)
                })
        
        logger.info(f"Found {len(property_values)} components with property '{property_name}'")
        return json.dumps(property_values, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})
    
@mcp.tool()
async def get_symbol_placement_rules(ctx: Context) -> str:
    """
    Get schematic symbol placement rules from a local configuration file
    
    Returns:
        str: JSON object with rules for placing pins on schematic symbols
    """
    logger.info("Getting symbol placement rules")
    
    # Define the rules file path in the MCP directory
    rules_file_path = MCP_DIR / "symbol_placement_rules.txt"
    
    # Check if the rules file exists
    if not rules_file_path.exists():
        logger.info("Symbol placement rules file not found, suggesting creation")
        
        # Default rules content
        default_rules = (
            "Your own pin placement rules, for example where power, ground, "
            "inputs, outputs and no-connect pins go and how pin groups are spaced."
        )
        
        # Create a helpful message for the user
        message = {
            "success": False,
            "error": f"Rules file not found at: {rules_file_path}",
            "message": f"Let the user know that they can optionally update the file {rules_file_path} with custom symbol placement rules. "
                      f"Suggested content: {default_rules}"
        }
        
        return json.dumps(message, indent=2)
    
    # Read the rules file if it exists
    try:
        with open(rules_file_path, "r") as f:
            rules_content = f.read()
        
        logger.info("Successfully read symbol placement rules file")
        if not rules_content.strip():
            rules_content = """No symbol placement rules are configured. Follow the conventions of a comparable symbol in the user's library (find one with search_library_symbol and read it with get_symbol_primitives) and any instructions from the user. Keep pins on a 100 mil grid so wires connect to them."""
        
        # Return the rules with a message about how to modify them
        result = {
            "success": True,
            "message": f"Modify {rules_file_path} with custom symbol placement instructions",
            "rules": rules_content
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error reading symbol placement rules file: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to read rules file: {str(e)}"
        }, indent=2)

@mcp.tool()
async def get_library_symbol_reference(ctx: Context) -> str:
    """
    Get the currently open symbol from a schematic library to use as reference for creating a new symbol.
    This tool should be used before creating a new symbol to understand the structure of existing symbols.
    
    Returns:
        str: JSON object with the reference symbol data including pins, their types, positions, and orientations
    """
    logger.info("Getting library symbol reference data")
    
    # Execute the command in Altium to get symbol reference data
    response = await altium_bridge.execute_command(
        "get_library_symbol_reference", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting symbol reference: {error_msg}")
        return json.dumps({"error": f"Failed to get symbol reference: {error_msg}"})
    
    # Get the symbol reference data
    symbol_data = response.get("result", {})
    
    if not symbol_data:
        logger.info("No symbol reference data found")
        return json.dumps({"error": "No symbol reference data found or no symbol is currently selected in the library"})
    
    logger.info(f"Retrieved symbol reference data")
    return json.dumps(symbol_data, indent=2)

@mcp.tool()
async def search_library_symbol(ctx: Context, symbol_name: str, library_path: str = "") -> str:
    """
    Search for a symbol by name in a schematic library (.SchLib) and navigate to it.
    Supports partial name matching (case-insensitive). Returns all matches and navigates
    to the best match (exact match preferred, otherwise first partial match).

    This tool will automatically open the library file in Altium if a path is provided,
    so no SchLib needs to be open beforehand.

    Args:
        symbol_name (str): Name or partial name of the symbol to search for
        library_path (str): Full file path to the .SchLib file (e.g. "C:\\Libraries\\MyParts.SchLib").
                           The tool will open this file in Altium if it is not already open.
                           If empty, uses the currently open library.
                           If no library is open and no path is provided, ask the user for the file path.

    Returns:
        str: JSON object with search results including matches, navigated symbol, and full symbol list
    """
    logger.info(f"Searching for symbol: {symbol_name} in library: {library_path or '(current)'}")

    # Execute the command in Altium
    params = {"symbol_name": symbol_name}
    if library_path:
        params["library_path"] = library_path

    response = await altium_bridge.execute_command(
        "search_library_symbol",
        params
    )

    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error searching for symbol: {error_msg}")
        return json.dumps({"error": f"Failed to search for symbol: {error_msg}"})

    # Get the result data
    result = response.get("result", {})

    if not result:
        logger.info("No search results returned")
        return json.dumps({"error": "No results returned from symbol search"})

    logger.info(f"Symbol search complete. Found: {result.get('found', False)}")
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_schematic_symbol(ctx: Context, symbol_name: str, description: str, pins: list, part_count: int = 1, graphics: list = None) -> str:
    r"""
    Before executing, run get_symbol_placement_rules first.

    For Altium API guidance while scripting, use the "altium-script" skill
    (ensure_altium_script_skill reports whether it is installed).

    Also look for a similar existing symbol to use as a style reference
    before drawing: search a company/library .SchLib for a comparable part
    (same category - op-amp, comparator, MCU, regulator, diode...) with
    search_library_symbol, then dump it with get_symbol_primitives and
    mirror its conventions (body style, pin lengths, pin name visibility,
    grid spacing, glyph shapes). This produces symbols consistent with the
    user's library. If no symbol library is available to reference, that is
    fine - skip this step and proceed with the defaults below; do not treat
    a missing reference as an error.

    Create a new schematic symbol in the current library with the specified pins
    Instructions: follow the rules from get_symbol_placement_rules and the
                  reference symbol's conventions; keep pins on a 100 mil grid

    Pin name inversion/overbar: To show an overbar on a pin name (for active-low signals),
                  place a backslash after EACH character that should be overbarred.
                  Examples: R\E\S\E\T\ renders as RESET with overbar.
                           C\S\/A0 renders as CS with overbar followed by /A0 without overbar.
                  Do NOT use ~{...} or other notation — only the backslash-per-character format works in Altium.

    Args:
        symbol_name (str): Name of the symbol to create
        description (str): Description of the schematic symbol
        pins (list): List of pin data in format
                    "pin_number|pin_name|pin_type|pin_orientation|x|y[|owner_part_id[|length[|show_name[|show_designator]]]]"
                    Pin types: eElectricHiZ, eElectricInput, eElectricIO, eElectricOpenCollector,
                               eElectricOpenEmitter, eElectricOutput, eElectricPassive, eElectricPower
                    Pin orientations: eRotate0 (right), eRotate90 (down), eRotate180 (left), eRotate270 (up)
                    X,Y coordinates in mils
                    owner_part_id (optional): Part number the pin belongs to (1-based).
                               Use 0 for pins shared across all parts (e.g. power/GND).
                               Defaults to 1 if omitted. Only needed for multi-part symbols.
                    length (optional): pin length in mils (default 300)
                    show_name / show_designator (optional): 1 or 0 to show/hide
                               the pin name / number (e.g. op-amp pins often hide names)
        part_count (int): Number of parts in the symbol (default 1).
                         Use >1 for multi-part symbols like quad op-amps or hex buffers.
        graphics (list, optional): Explicit body graphics. When given, the
                    default auto-sized body rectangle is NOT drawn - the
                    graphics fully define the symbol body (triangles for
                    op-amps, diode glyphs, etc.). Entry formats (coordinates
                    in mils; part = owner part id, 1-based; width 0-3 =
                    zero/small/medium/large; solid 1 or 0):
                    - "line|part|width|x1|y1|x2|y2"
                    - "polyline|part|width|x1|y1|x2|y2|..." (any number of vertices)
                    - "polygon|part|width|solid|x1|y1|x2|y2|..." (closed/filled shape)
                    - "rectangle|part|width|solid|x1|y1|x2|y2"
                    - "arc|part|width|cx|cy|radius|start_angle|end_angle" (degrees CCW from 3 o'clock)
                    - "elliptical_arc|part|width|cx|cy|radius|secondary_radius|start_angle|end_angle"
                    - "ellipse|part|width|solid|cx|cy|radius|secondary_radius"
                    - "label|part|x|y|text" (free text annotation)
                    Tip: to reproduce an existing symbol's style, dump it first
                    with get_symbol_primitives and mirror its primitives.

    Returns:
        str: JSON object with the result of the component creation
    """
    logger.info(f"Creating schematic symbol: {symbol_name} with {len(pins)} pins, {part_count} part(s)")

    params = {
        "symbol_name": symbol_name,
        "description": description,
        "part_count": part_count,
        "pins": pins
    }
    if graphics:
        params["graphics"] = graphics

    # Execute the command in Altium to create the symbol
    response = await altium_bridge.execute_command(
        "create_schematic_symbol",
        params
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating symbol: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create symbol: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Symbol {symbol_name} created successfully with {len(pins)} pins")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_schematic_data(ctx: Context, cmp_designators: list) -> str:
    """
    Get schematic data for components in Altium
    
    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])
    
    Returns:
        str: JSON object with schematic component data for requested designators
    """
    logger.info(f"Getting schematic data for components: {cmp_designators}")
    
    # Execute the command in Altium to get schematic data
    response = await altium_bridge.execute_command(
        "get_schematic_data",
        {}  # No parameters needed for this command in the Altium script
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting schematic data: {error_msg}")
        return json.dumps({"error": f"Failed to get schematic data: {error_msg}"})
    
    # Get the schematic data
    schematic_data = response.get("result", [])
    
    if not schematic_data:
        logger.info("No schematic data found")
        return json.dumps({"error": "No schematic data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(schematic_data, str):
            schematic_list = json.loads(schematic_data)
        else:
            schematic_list = schematic_data
        
        # Filter components by designator
        components = []
        missing_designators = []
        
        for designator in cmp_designators:
            found = False
            for component in schematic_list:
                if component.get("designator") == designator:
                    components.append(component)
                    found = True
                    break
            
            if not found:
                missing_designators.append(designator)
        
        result = {
            "components": components,
        }
        
        if missing_designators:
            result["missing_designators"] = missing_designators
            logger.info(f"Some designators not found in schematic data: {missing_designators}")
        
        logger.info(f"Found schematic data for {len(components)} components")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error processing schematic data: {e}")
        return json.dumps({"error": f"Failed to process schematic data: {str(e)}"})
    
@mcp.tool()
async def get_pcb_layers(ctx: Context) -> str:
    """
    Get detailed information about all layers in the current Altium PCB
    
    Returns:
        str: JSON object with detailed layer information including copper layers, 
             mechanical layers, and special layers with their properties
    """
    logger.info("Getting detailed PCB layer information")
    
    # Execute the command in Altium to get all layers data
    response = await altium_bridge.execute_command(
        "get_pcb_layers",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB layers: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB layers: {error_msg}"})
    
    # Get the layers data
    layers_data = response.get("result", [])
    
    if not layers_data:
        logger.info("No PCB layers found")
        return json.dumps({"message": "No PCB layers found in the current document"})
    
    logger.info(f"Retrieved PCB layers data")
    return json.dumps(layers_data, indent=2)

@mcp.tool()
async def set_pcb_layer_visibility(ctx: Context, layer_names: list, visible: bool) -> str:
    """
    Set visibility for specified PCB layers
    
    Args:
        layer_names (list): List of layer names to modify (e.g., ["Top Layer", "Bottom Layer", "Mechanical 1"])
        visible (bool): Whether to show (True) or hide (False) the specified layers
        
    Returns:
        str: JSON object with the result of the operation
    """
    logger.info(f"Setting layers visibility: {layer_names} to {visible}")
    
    # Execute the command in Altium to set layer visibility
    response = await altium_bridge.execute_command(
        "set_pcb_layer_visibility",
        {
            "layer_names": layer_names,
            "visible": visible
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error setting layer visibility: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to set layer visibility: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Layer visibility set successfully")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_component_data(ctx: Context, cmp_designators: list) -> str:
    """
    Get all data for components in Altium
    
    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])
    
    Returns:
        str: JSON object with all component data for requested designators
    """
    logger.info(f"Getting data for components: {cmp_designators}")
    
    # Execute the command in Altium to get all component data
    response = await altium_bridge.execute_command(
        "get_all_component_data",
        {}  # No parameters needed for this command in the Altium script
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    component_data = response.get("result", [])
    
    if not component_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        
        # Filter components by designator
        components = []
        missing_designators = []
        
        for designator in cmp_designators:
            found = False
            for component in component_list:
                if component.get("designator") == designator:
                    components.append(component)
                    found = True
                    break
            
            if not found:
                missing_designators.append(designator)
        
        result = {
            "components": components,
        }
        
        if missing_designators:
            result["missing_designators"] = missing_designators
            logger.info(f"Some designators not found: {missing_designators}")
        
        logger.info(f"Found data for {len(components)} components")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_selected_components_coordinates(ctx: Context) -> str:
    """
    Get coordinates and positioning information for selected components in Altium layout
    
    Returns:
        str: JSON array with positioning data (designator, x, y, rotation, width, height)
    """
    logger.info("Getting coordinates for selected components")
    
    # Execute the command in Altium to get selected components coordinates
    response = await altium_bridge.execute_command(
        "get_selected_components_coordinates",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting selected components coordinates: {error_msg}")
        return json.dumps({"error": f"Failed to get selected components coordinates: {error_msg}"})
    
    # Get the components coordinates data
    components_coords = response.get("result", [])
    
    if not components_coords:
        logger.info("No selected components found")
        return json.dumps({"message": "No components are currently selected in the layout"})
    
    logger.info(f"Retrieved positioning data for selected components")
    return json.dumps(components_coords, indent=2)

@mcp.tool()
async def get_all_designators(ctx: Context) -> str:
    """
    Get all component designators from the current Altium board
    
    Returns:
        str: JSON array of all component designators on the current board
    """
    logger.info("Getting all component designators")
    
    # Execute the command in Altium to get all component data
    response = await altium_bridge.execute_command(
        "get_all_component_data",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    component_data = response.get("result", [])
    
    if not component_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        
        # Extract designators
        designators = [comp.get("designator") for comp in component_list if "designator" in comp]
        
        logger.info(f"Found {len(designators)} designators")
        return json.dumps(designators)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_component_pins(ctx: Context, cmp_designators: list) -> str:
    """
    Get pin data for components in Altium

    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])

    Returns:
        str: JSON array, one entry per component with its placement info
             (x/y in mils relative to the board origin, rotation in degrees
             counterclockwise, layer) and a "pins" list. Per pin:
             - x/y: absolute pad position (mils, relative to board origin)
             - dx/dy: pad offset from the component origin in the footprint's
               rotation-0 frame. To predict a pad position for a planned
               placement: mirror dx (dx = -dx) if placing on the bottom layer,
               rotate (dx, dy) counterclockwise by the planned rotation, then
               add the planned component x/y.
             - rotation: the pad's own rotation (NOT the component rotation)
             - net, layer, width, height, shape
    """
    logger.info(f"Getting pin data for components: {cmp_designators}")
    
    # Execute the command in Altium to get pin data
    response = await altium_bridge.execute_command(
        "get_component_pins",
        {"designators": cmp_designators}  # Pass the list of designators
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting pin data: {error_msg}")
        return json.dumps({"error": f"Failed to get pin data: {error_msg}"})
    
    # Get the components pins data
    pins_data = response.get("result", [])
    
    if not pins_data:
        logger.info(f"No pin data found for designators: {cmp_designators}")
        return json.dumps({"message": "No pin data found for the specified components"})
    
    logger.info(f"Retrieved pin data for components")
    return json.dumps(pins_data, indent=2)

SANDBOX_DIR = MCP_DIR / "SandboxScript"
SANDBOX_PAS = SANDBOX_DIR / "Sandbox.pas"
SANDBOX_PRJ = SANDBOX_DIR / "Sandbox.PrjScr"
SANDBOX_RUN = EXCHANGE_DIR / "sandbox_run.pas"     # Sandbox.pas with the experiment injected
SANDBOX_LOG = EXCHANGE_DIR / "sandbox_log.txt"
SANDBOX_RESULT = EXCHANGE_DIR / "sandbox_result.json"
SANDBOX_BEGIN = "// === BEGIN EXPERIMENT"
SANDBOX_END = "// === END EXPERIMENT"


def _dismiss_altium_dialogs():
    """Close Altium modal popups that would otherwise block a script run.

    Altium uses two kinds: Win32 task dialogs (#32770) and Delphi TMessageForm
    error/warning boxes.
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return 0
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "#32770":
            found.append(hwnd)
        elif cls.value == "TMessageForm":
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value in ("Error", "Warning", "Information", "Confirm"):
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for h in found:
        user32.PostMessageW(h, 0x0010, 0, 0)
    return len(found)


@mcp.tool()
async def run_altium_script(ctx: Context, script: str, timeout_seconds: int = 120) -> str:
    """
    Run a DelphiScript snippet inside an isolated Altium sandbox and report
    what happened, step by step.

    Use this to develop and verify Altium API code before relying on it.
    Altium has no headless test mode and its failure modes are hostile: a
    runtime error leaves the script PAUSED IN THE DEBUGGER with no dialog,
    after which every later script run silently does nothing until the
    debugger is stopped (Ctrl+F3) or Altium is restarted. This tool detects
    that state and reports exactly which statement died.

    The script runs as a SEPARATE script file, so a crash here can never
    break the other MCP tools.

    Writing the script:
    - Call SandboxLog('...') before each risky statement. The log is flushed
      after every call, so the last logged line identifies what failed.
    - Assign findings to the string variable ResultText - it is returned.
    - DelphiScript has NO inline variable declarations. Reuse the provided
      scratch variables: S1..S3 (String), I1..I3 and B1 (Integer),
      Obj1..Obj5 (IDispatch), List1 (TStringList), IntMan, DbDoc.
    - try/except does NOT catch runtime errors such as bad conversions or
      invalid API calls, so it cannot be relied on to keep a script alive.
    - The sandbox is standalone: helpers and constants from the production
      units (TrimJSON, AddJSONProperty, ...) are NOT available; REPLACEALL is.
    - Never register objects into a library document and never write to shared
      or network library paths. Verify the target document kind first
      (ObjectID 32 = schematic, 33 = symbol library).

    For API guidance - interfaces, object models, worked examples - use the
    "altium-script" skill. ensure_altium_script_skill reports whether that
    skill is installed and can install it.

    Args:
        script (str): DelphiScript statements to execute (body only).
        timeout_seconds (int): How long to wait for completion (default 120).

    Returns:
        str: JSON with success, the step log, the script's ResultText, and on
             failure the last step reached plus whether Altium's script
             executor is now wedged and needs recovery.
    """
    logger.info(f"run_altium_script: {len(script.splitlines())} lines")

    if not SANDBOX_PAS.exists():
        return json.dumps({"success": False,
                           "error": f"sandbox script missing at {SANDBOX_PAS}"})

    problem = altium_limit_problem()
    if problem:
        return json.dumps({"success": False, "error": problem})

    try:
        src = SANDBOX_PAS.read_text(encoding="utf-8")
        pre, rest = src.split(SANDBOX_BEGIN, 1)
        marker_line, rest = rest.split("\n", 1)
        _, post = rest.split(SANDBOX_END, 1)
        body = "\n".join("        " + ln if ln.strip() else ln
                          for ln in script.strip("\n").splitlines())
        SANDBOX_RUN.write_text(
            pre + SANDBOX_BEGIN + marker_line + "\n" + body + "\n        " + SANDBOX_END + post,
            encoding="utf-8")
    except Exception as e:
        return json.dumps({"success": False, "error": f"could not inject script: {e}"})

    for f in (SANDBOX_LOG, SANDBOX_RESULT):
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass

    # A script file, not the project: running a project leaks window handles
    cmd = (f'"{altium_bridge.config.altium_exe_path}" -RScriptingSystem:RunScriptFile('
           f'FileName={SANDBOX_RUN}^|ProcName=Run)')
    subprocess.Popen(cmd, shell=True)

    start = time.time()
    dialogs = 0
    while not SANDBOX_RESULT.exists() and time.time() - start < timeout_seconds:
        await asyncio.sleep(0.5)
        if time.time() - start > 6:
            dialogs += _dismiss_altium_dialogs()

    steps = []
    if SANDBOX_LOG.exists():
        steps = SANDBOX_LOG.read_text(encoding="utf-8", errors="replace").splitlines()

    if SANDBOX_RESULT.exists():
        result_text = SANDBOX_RESULT.read_text(encoding="utf-8", errors="replace").strip()
        return json.dumps({"success": True, "result": result_text, "steps": steps,
                           "dialogs_dismissed": dialogs}, indent=2)

    if steps:
        return json.dumps({
            "success": False,
            "error": "script started but did not finish",
            "last_step_reached": steps[-1],
            "diagnosis": "The statement AFTER the last step is what crashed or paused the script.",
            "executor_wedged": True,
            "recovery": "Altium's script executor is now blocked. Recover by running this "
                        "shell command (sends the debugger Stop process to the running "
                        "Altium): \"<altium_exe>\" -REditScript:Stop  -- then retry.",
            "steps": steps,
            "dialogs_dismissed": dialogs}, indent=2)

    return json.dumps({
        "success": False,
        "error": "script never started",
        "diagnosis": "Usually a COMPILE error in the script, or a previously paused "
                     "script blocking execution.",
        "executor_wedged": True,
        "recovery": "A previously paused script may be blocking execution. Recover by "
                    "running this shell command: \"<altium_exe>\" -REditScript:Stop  "
                    "-- then retry. If it still fails, the script itself has a COMPILE error.",
        "dialogs_dismissed": dialogs}, indent=2)


@mcp.tool()
async def ensure_altium_script_skill(ctx: Context, install: bool = False) -> str:
    """
    Check whether the "altium-script" skill is installed, and optionally
    install it.

    That skill documents the Altium DelphiScript API - interfaces, object
    models, worked examples, conventions, and how to discover undocumented
    processes - and is the reference to consult before writing scripts for
    run_altium_script or debugging Altium API calls.

    Source: https://github.com/coffeenmusic/altium-scripts-skill

    Installing writes into the user's skills directory, so it only happens when
    install=True is passed explicitly. Skills load at client startup, so a
    newly installed skill becomes available after restarting the client.

    Args:
        install (bool): Install the skill if missing (requires git on PATH).

    Returns:
        str: JSON with installed (bool), the path checked, and the next step.
    """
    skill_dir = Path.home() / ".claude" / "skills" / "altium-script"
    repo = "https://github.com/coffeenmusic/altium-scripts-skill"

    if (skill_dir / "SKILL.md").exists():
        return json.dumps({"installed": True, "path": str(skill_dir),
                           "note": "Use the altium-script skill for API guidance."},
                          indent=2)

    if not install:
        return json.dumps({
            "installed": False,
            "path": str(skill_dir),
            "source": repo,
            "next_step": "Call again with install=true to clone it, or install manually "
                         "into the path above."}, indent=2)

    try:
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", "clone", "--depth", "1", repo, str(skill_dir)],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return json.dumps({
                "installed": False, "path": str(skill_dir),
                "error": (proc.stderr or proc.stdout)[:400],
                "hint": f"Requires git on PATH; otherwise download {repo} and extract "
                        "to the path above."}, indent=2)
        return json.dumps({"installed": True, "path": str(skill_dir), "source": repo,
                           "next_step": "Restart the client so the skill is loaded."},
                          indent=2)
    except Exception as e:
        return json.dumps({"installed": False, "path": str(skill_dir),
                           "error": str(e)[:300]}, indent=2)


@mcp.tool()
async def get_footprint_primitives(ctx: Context, library_path: str = "", footprint_name: str = "") -> str:
    """
    Read the primitives of footprints in a PCB library (.PcbLib).

    Modes:
    - footprint_name omitted: inventory of every footprint with per-type
      primitive counts (pads, tracks, arcs, fills, texts, regions, vias,
      component_bodies)
    - footprint_name given (exact, case-insensitive): full geometry dump -
      pads (position, rotation, layer, sizes/shape per stack, hole size/
      type/width/rotation, plating, paste_mode/paste_expansion,
      mask_mode/mask_expansion, locked), tracks, arcs, fills, texts,
      regions (outline vertices). Coordinates in mils relative to the
      footprint origin (also for the footprint shown in the editor, which
      Altium holds offset by the library origin); shapes and hole types as
      raw Altium enum ints; layers as names. 3D component bodies are listed
      apart in bodies_3d, not among the primitives, so the
      create_footprints_batch round-trip is unchanged: model_file,
      model_embedded, model_x/model_y (offset from the footprint origin),
      standoff_height and overall_height (mils), layer, and
      rotation_x/y/z (degrees) and model_z_offset (mils). The rotations and
      offset come from the saved library file (Altium's script API cannot
      return them); "placement" says so, and they are null for a body that
      is not saved yet.
    - footprint_name "*": full dump of every footprint

    Use as the reference when recreating or validating footprints, and to
    survey what a library requires.

    Args:
        library_path (str, optional): Full path to the .PcbLib. Omit to use
            the currently focused PCB library (an already-open library is
            only focused, never reloaded).
        footprint_name (str, optional): Exact footprint name, or "*".

    Returns:
        str: JSON - inventory: {library_name, footprint_count, footprints:
             [{name, description, <type counts>}]}; dump: primitives and
             bodies_3d per footprint
    """
    logger.info(f"Getting footprint primitives (library={library_path}, footprint={footprint_name})")

    response = await altium_bridge.execute_command(
        "get_footprint_primitives",
        {"library_path": library_path, "footprint_name": footprint_name}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        return json.dumps({"success": False, "error": f"Failed to get footprint primitives: {error_msg}"})

    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result
    if isinstance(result, dict):
        pcblib_file.add_body_placements(result)
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_footprints_batch(ctx: Context, spec_file: str) -> str:
    """
    Create many PCB footprints in a single Altium script run.

    The batch equivalent of create_pcb_footprint with far broader coverage:
    through-hole and SMD pads (full pad stack, holes, slots, plating,
    rotation), tracks, arcs, fills, texts, and regions on any layer.
    Verified by exact round-trips of complete production footprint
    libraries. Prefer this for bulk imports/migrations; use
    get_footprint_primitives on an existing footprint to learn the exact
    field conventions.

    Args:
        spec_file (str): Path to a plain-text spec file, one record per line
            (coords in mils, layers as names, shapes/hole types as raw
            Altium enum ints, booleans as 1/0):
            FPLIB|<path to .PcbLib>   (optional first line: opens/focuses)
            FOOTPRINT|<name>|<description>
            EDITFOOTPRINT|<name>      add MODEL3D records to an existing
                                      footprint (never creates one; other
                                      records under it are reported and
                                      ignored)
            PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape[|paste_mode|paste_expansion|mask_mode|mask_expansion]]]
                Leave a field empty to skip it and still give a later one.
                paste_mode/mask_mode are TMaskExpansionMode ints: 1 rule,
                2 manual (with the expansion in mils). 0 (no mask) cannot
                be set on a pad by script and is reported as a problem.
                Omitted: rule. Every pad the tool creates is locked.
            TRACK|x1|y1|x2|y2|width|layer
            ARC|cx|cy|radius|start_angle|end_angle|width|layer
            FILL|x1|y1|x2|y2|rotation|layer
            TEXT|x|y|size|width|rotation|layer|mirror|ttf|text
            REGION|layer|kind|x1|y1|x2|y2|...
            MODEL3D|<STEP file>|<layer>|<rot_x>|<rot_y>|<rot_z>|<z_offset>[|<x>|<y>[|<embed 1/0>]]
                add a 3D body from a STEP file: rotations in degrees,
                z_offset (model height above the board) and x|y (offset
                from the footprint origin) in mils, embedded in the
                library unless embed is 0. Read the result back with
                get_footprint_primitives (bodies_3d); its rotations and
                z offset appear only once the library is saved.

    The library is left modified and unsaved; the user saves it.

    Returns:
        str: JSON with created and edited counts, models_added,
             primitive_errors, failed names and problems (e.g. a missing
             model file, mask mode 0, a record EDITFOOTPRINT ignores)
    """
    logger.info(f"Creating footprints batch from {spec_file}")

    response = await altium_bridge.execute_command(
        "create_footprints_batch",
        {"spec_file": spec_file}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        return json.dumps({"success": False, "error": f"Failed batch footprint creation: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def build_schematic(ctx: Context, parts: list, wires: list = None,
                          junctions: list = None, net_labels: list = None,
                          power_ports: list = None, notes: list = None) -> str:
    """
    Build a wired schematic on a NEW sheet from a circuit description.

    READ dev/SCHEMATIC_CONVENTIONS.md BEFORE CALLING. This tool places exactly
    what it is given; it does not lay out or correct spacing. The conventions
    file records the drafting rules that make the result readable, each one
    learned by getting it wrong. The ones that most often produce a plausible
    but wrong sheet:

      * A pin's connection point is NOT Pin.Location - it is PinLength further
        along the pin. Do not guess coordinates. Call with parts only first,
        read the returned pin_map, then call again with wires routed from those
        measured coordinates.
      * Never run a riser in a pin's connection column; it drives through every
        pin sharing that column.
      * Give a shunt part one grid of wire between its pin and the node it taps,
        rather than putting the junction on the pin.
      * A net label that does not TOUCH its wire names nothing.
      * A shunt part occupies the band below its rail. Do not route another net
        through that band - the wire will pass through the symbol body.
      * A ground port's "GND" label is always drawn (ShowNetName cannot hide it)
        and occupies ~300 mil below the port. Keep wires out of that band.

    Args:
        parts: component dicts. Required keys: designator, symbol_library,
            symbol, x, y (mils). Optional: design_item_id, orientation (0-3),
            mirror (0/1), comment, description, footprint,
            parameters ({name: value}).
        wires: each a flat list of alternating x,y in mils, e.g.
            [3900, 3000, 5300, 3000, 5300, 4900] - two segments, three points.
        junctions: [{"x":..,"y":..}] - only where 3+ branches actually meet.
        net_labels: [{"x":..,"y":..,"orientation":0,"text":"LED+"}] - the
            coordinate must lie on a wire.
        power_ports: [{"x":..,"y":..,"orientation":3,"style":5,"text":"GND",
            "show_net_name":false}]. Harvest style/orientation from an existing
            sheet rather than guessing: 5 = digital ground, 2 = supply bar.
        notes: [{"x":..,"y":..,"text":".."}] free text.

    Returns:
        JSON with the created sheet name, counts, and the pin map - every
        placed pin's true connection point, for routing a follow-up call.
    """
    logger.info(f"Building schematic: {len(parts)} parts")

    lines = []
    for p in parts:
        for key in ("designator", "symbol_library", "symbol", "x", "y"):
            if key not in p:
                return json.dumps({"success": False,
                                   "error": f"part missing required key '{key}': {p}"})
        lines.append("PART|{}|{}|{}|{}|{}|{}|{}|{}".format(
            p["designator"], p["symbol_library"], p["symbol"],
            p.get("design_item_id", ""), int(p["x"]), int(p["y"]),
            int(p.get("orientation", 0)), 1 if p.get("mirror") else 0))
        if p.get("comment"):
            lines.append(f"COMMENT|{p['comment']}")
        if p.get("footprint"):
            lines.append(f"FOOTPRINT|{p['footprint']}")
        if p.get("description"):
            lines.append(f"DESCRIPTION|{p['description']}")
        for name, val in (p.get("parameters") or {}).items():
            lines.append(f"PARAM|{name}|{val}")

    for route in (wires or []):
        if len(route) < 4 or len(route) % 2:
            return json.dumps({"success": False,
                               "error": f"wire needs an even count of >=4 coords: {route}"})
        lines.append("WIRE|" + "|".join(str(int(v)) for v in route))
    for j in (junctions or []):
        lines.append(f"JUNCTION|{int(j['x'])}|{int(j['y'])}")
    for n in (net_labels or []):
        lines.append("NETLABEL|{}|{}|{}|{}".format(
            int(n["x"]), int(n["y"]), int(n.get("orientation", 0)), n["text"]))
    for pw in (power_ports or []):
        lines.append("POWER|{}|{}|{}|{}|{}|{}".format(
            int(pw["x"]), int(pw["y"]), int(pw.get("orientation", 3)),
            int(pw.get("style", 5)), pw["text"],
            1 if pw.get("show_net_name") else 0))
    for nt in (notes or []):
        lines.append(f"NOTE|{int(nt['x'])}|{int(nt['y'])}|{nt['text']}")

    spec_path = Path("C:/Users/Public/altium_mcp/circuit_spec.txt")
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    # cp1252: the bridge reads these as ANSI, and part descriptions carry
    # characters that are not plain ASCII
    spec_path.write_text("\n".join(lines) + "\n", encoding="cp1252", errors="replace")

    response = await altium_bridge.execute_command("build_circuit", {})
    if not response.get("success", False):
        return json.dumps({"success": False,
                           "error": response.get("error", "unknown error")})

    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result

    # Hand back the measured pin map so the caller can route a second pass
    pin_map = {}
    pm = Path("C:/Users/Public/altium_mcp/pin_map.txt")
    if pm.is_file():
        for line in pm.read_text(errors="replace").splitlines():
            f = line.strip().split("|")
            if len(f) == 5 and f[0] == "PIN":
                pin_map.setdefault(f[1], {})[f[2]] = [int(f[3]), int(f[4])]
    result["pin_map"] = pin_map
    result["pin_map_note"] = ("Absolute electrical connection points. Route "
                              "wires from these, not from predicted offsets.")
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_symbols_batch(ctx: Context, spec_file: str) -> str:
    """
    Create many schematic symbols, or edit existing ones, in a single
    Altium script run.

    Use instead of repeated create_schematic_symbol calls when creating
    more than a handful of symbols (bulk library imports/migrations): one
    script launch instead of one per symbol, and pipe-delimited plain text
    instead of JSON so field text (commas, brackets, spaces) is preserved
    exactly. Verified by exact round-trips of a complete 284-symbol
    production library.

    Args:
        spec_file (str): Path to a plain-text spec file, one record per line:
            LIBRARY|<path to .SchLib>   (optional first line: opens/focuses;
                                         an already-open library is only
                                         focused, never reloaded)
            SYMBOL|<name>|<description>|<part_count>
            PIN|<same pipe fields as create_schematic_symbol pins>
            GRAPHIC|<same entry format as create_schematic_symbol graphics>
            EDITSYMBOL|<name>   edit an existing symbol instead (its pins
                                and graphics are left alone; a missing
                                symbol is reported as failed, never created)
            COMMENT|<text>[|<visible 1/0>]   set the Comment (empty text
                                is allowed); hidden unless visible is 1
            DESCRIPTION|<text>  set the Description
            SYMPARAM|<name>|<value>|<visible 1/0>[|x|y[|justification[|font]]]
                                add a symbol parameter, or replace one with
                                that name. By default, visible ones without
                                x|y go below the body's bottom-left corner,
                                left-aligned, 100 mil apart in record order
                                (leave a field empty to skip it and still
                                give a later one). justification:
                                bottom_left, bottom_center, bottom_right,
                                center_left, center, center_right, top_left,
                                top_center or top_right. font: <font
                                name>:<size>, e.g. "Arial:10" (resolved by
                                name, so it works in any document). Omitted
                                fields keep the parameter's own. A parameter
                                the tool positions gets Autoposition off, so
                                Altium does not move it later.
            PINPARAM|<pin number>|<name>|<value>   add (or replace) a hidden
                                parameter on every pin with that number
            PINDESC|<pin number>|<text>   set the Description of every pin
                                with that number
            PINSYMBOL|<pin number>|<edge>|<symbol>   set an IEEE symbol of
                                every pin with that number. edge: inside,
                                inside_edge, outside_edge or outside.
                                symbol: a TIeeeSymbol name without the
                                leading "e", any case - none, dot, clock,
                                activelowinput, activelowoutput, schmitt,
                                analogsignalin, digitalsignalin, opencollector,
                                hiz, ... (the full list is in the
                                altium-script skill, SCH_API_Reference.md,
                                TIeeeSymbol). An unknown edge or symbol is
                                reported under problems.
            These records work after SYMBOL and EDITSYMBOL alike.
            Each SYMBOL or EDITSYMBOL line starts a new symbol; the other
            records belong to the most recent one.

    Every created or edited symbol is one undo step and marks the library
    as modified; nothing is saved - the user saves the library.

    Returns:
        str: JSON object with created and edited counts, a failed name list
             and problems (e.g. a pin number not in the symbol, an unknown
             IEEE symbol or justification)
    """
    logger.info(f"Creating symbols batch from {spec_file}")

    response = await altium_bridge.execute_command(
        "create_symbols_batch",
        {"spec_file": spec_file}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error in batch symbol creation: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed batch creation: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def get_symbol_primitives(ctx: Context, library_path: str = "", symbol_name: str = "") -> str:
    """
    Read the graphic primitives of symbols in a schematic library (.SchLib).

    Two modes:
    - symbol_name omitted: inventory of every symbol in the library with
      per-type primitive counts (pins, rectangles, lines, polylines,
      polygons, arcs, ellipses, beziers, labels, ...). Use this to survey
      what drawing features a library's symbols require.
    - symbol_name given (exact match, case-insensitive): full geometry dump
      of that symbol - every primitive with coordinates in mils, plus pin
      details (number, name, electrical type, orientation, length,
      owner_part_id). Use this as the reference/spec when recreating or
      validating a symbol.

    Args:
        library_path (str, optional): Full path to the .SchLib file to open.
            Omit to use the schematic library currently focused in Altium.
        symbol_name (str, optional): Exact symbol (LibReference) name to dump.

    Returns:
        str: JSON object - inventory mode: {library_name, symbol_count,
             symbols: [{name, description, part_count, <type counts>}]};
             dump mode: {library_name, symbol_name, description, part_count,
             comment: {text, visible}, parameters: [{name, value, visible,
             x, y, justification, autoposition, font: {name, size}}],
             primitives: [...]} - x and y are where Altium holds the
             parameter now. Pins also carry
             description, symbol_inside, symbol_inside_edge,
             symbol_outside_edge and symbol_outside (IEEE symbol names as
             PINSYMBOL takes them), and their parameters [{name, value}]
             when they have any
    """
    logger.info(f"Getting symbol primitives (library={library_path}, symbol={symbol_name})")

    response = await altium_bridge.execute_command(
        "get_symbol_primitives",
        {"library_path": library_path, "symbol_name": symbol_name}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting symbol primitives: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to get symbol primitives: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def get_all_nets(ctx: Context) -> str:
    """
    Return every unique net name in the active PCB document.

    Returns
    -------
    str :
        A JSON array of net names, e.g. ["GND", "VCC33", "USB_D+", ...]
    """
    logger.info("Getting all nets")

    response = await altium_bridge.execute_command("get_all_nets", {})

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting nets: {error_msg}")
        return json.dumps({"error": f"Failed to get nets: {error_msg}"})

    # Result is already a JSON‑serialisable Python list
    return json.dumps(response.get("result", []), indent=2)

@mcp.tool()
async def create_net_class(ctx: Context, class_name: str, net_names: list) -> str:
    """
    Create a new net class and add specified nets to it
    
    Args:
        class_name (str): Name of the net class to create or modify
        net_names (list): List of net names to add to the class
    
    Returns:
        str: JSON object with the result of the operation
    """
    logger.info(f"Creating net class '{class_name}' with {len(net_names)} nets")
    
    # Execute the command in Altium to create the net class
    response = await altium_bridge.execute_command(
        "create_net_class",
        {
            "class_name": class_name,
            "net_names": net_names
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating net class: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create net class: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Net class '{class_name}' created/modified successfully")
    return json.dumps(result, indent=2)
    
@mcp.tool()
async def set_component_position(ctx: Context, cmp_designator: str, x: float, y: float, rotation: float = -1) -> str:
    """
    Set a component's absolute position in the PCB layout
    
    Args:
        cmp_designator (str): Designator of the component to position (e.g., "R1", "C5", "U3")
        x (float): Absolute X position in mils
        y (float): Absolute Y position in mils
        rotation (float): Rotation angle in degrees (0-360), use -1 to keep current rotation
    
    Returns:
        str: JSON object with the result of the position operation
    """
    logger.info(f"Setting component {cmp_designator} position to X:{x}, Y:{y}, Rotation:{rotation}")
    
    response = await altium_bridge.execute_command(
        "set_component_position",
        {
            "designator": cmp_designator,
            "x": x,
            "y": y,
            "rotation": rotation
        }
    )
    
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error setting component position: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to set component position: {error_msg}"})
    
    result = response.get("result", {})
    logger.info(f"Component position set successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

@mcp.tool()
async def move_components(ctx: Context, cmp_designators: list, x_offset: float, y_offset: float, rotation: float = 0) -> str:
    """
    Move components by RELATIVE offset from their current position (not absolute positioning)
    
    IMPORTANT: This moves components BY the offset amount, not TO a position.
    For absolute positioning, use set_component_position instead.
    
    Args:
        cmp_designators (list): List of designators of the components to move (e.g., ["R1", "C5", "U3"])
        x_offset (float): X offset distance in mils (positive = right, negative = left)
        y_offset (float): Y offset distance in mils (positive = up, negative = down)
        rotation (float): New absolute rotation angle in degrees (0-360), if 0 the rotation is not changed
    
    Returns:
        str: JSON object with the result of the move operation
    """
    logger.info(f"Moving components: {cmp_designators} by X:{x_offset}, Y:{y_offset}, Rotation:{rotation}")
    
    # Execute the command in Altium to move components
    response = await altium_bridge.execute_command(
        "move_components",
        {
            "designators": cmp_designators,
            "x_offset": x_offset,
            "y_offset": y_offset,
            "rotation": rotation
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error moving components: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to move components: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})

    logger.info(f"Components moved successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

@mcp.tool()
async def place_components(ctx: Context, placements: list) -> str:
    """
    Place multiple components at absolute positions in a single Altium transaction.

    This is the batch version of set_component_position - prefer it whenever
    placing more than one component, since every tool call is a full round
    trip into Altium. The whole batch is one undo step.

    Coordinate conventions: x/y are in mils relative to the board origin;
    rotation is in degrees counterclockwise.

    Args:
        placements (list): One dict per component:
            - designator (str, required): e.g. "R1"
            - x (float, required): absolute X position in mils
            - y (float, required): absolute Y position in mils
            - rotation (float, optional): absolute rotation in degrees (0-360).
              Omit (or pass -1) to keep the current rotation.
            - layer (str, optional): "top" or "bottom" to set the board side
              (the footprint is mirrored when flipped). Omit to keep the
              current side.

    Example:
        placements=[{"designator": "R1", "x": 1000, "y": 2000, "rotation": 90},
                    {"designator": "C5", "x": 1050, "y": 2000, "layer": "bottom"}]

    Returns:
        str: JSON object with placed_count, missing_designators, and the final
             x/y/rotation/layer of each placed component as Altium reports them
    """
    logger.info(f"Placing {len(placements)} components")

    # Flatten each placement into a pipe-delimited string
    # ('Designator|X|Y|Rotation|Layer') - the DelphiScript side parses the
    # request line by line, so nested JSON objects are not safe to send
    entries = []
    errors = []
    for idx, placement in enumerate(placements):
        if not isinstance(placement, dict):
            errors.append(f"placements[{idx}] must be an object")
            continue

        designator = str(placement.get("designator", "")).strip()
        x = placement.get("x")
        y = placement.get("y")

        if not designator or x is None or y is None:
            errors.append(f"placements[{idx}] must include designator, x, and y")
            continue
        if "|" in designator:
            errors.append(f"placements[{idx}] designator must not contain '|'")
            continue

        rotation = placement.get("rotation", -1)
        layer = str(placement.get("layer", "") or "").strip().lower()
        if layer not in ("", "top", "bottom"):
            errors.append(f"placements[{idx}] layer must be 'top' or 'bottom'")
            continue

        try:
            entry = f"{designator}|{float(x)}|{float(y)}|{float(rotation)}|{layer}"
        except (TypeError, ValueError):
            errors.append(f"placements[{idx}] x, y, and rotation must be numbers")
            continue
        entries.append(entry)

    if errors:
        return json.dumps({"success": False, "error": "; ".join(errors)})
    if not entries:
        return json.dumps({"success": False, "error": "No placements provided"})

    response = await altium_bridge.execute_command(
        "place_components",
        {"placements": entries}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error placing components: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to place components: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Placed components successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

def _mst_length(points: list) -> float:
    """Minimum-spanning-tree length over (x, y) points (Prim's algorithm)."""
    n = len(points)
    if n < 2:
        return 0.0
    import math
    in_tree = [False] * n
    best = [float("inf")] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i])
        in_tree[u] = True
        total += best[u]
        ux, uy = points[u]
        for v in range(n):
            if not in_tree[v]:
                d = math.dist((ux, uy), points[v])
                if d < best[v]:
                    best[v] = d
    return total

@mcp.tool()
async def get_net_connections(ctx: Context, cmp_designators: list = None, max_pads_per_net: int = 40) -> str:
    """
    Get net connectivity and airline (unrouted connection) lengths for the
    nets touching the given components.

    Use this to plan or score a placement: it shows every pad on each net -
    including pads of OTHER components outside the given set (e.g. an input
    filter the cluster must connect to) - plus the net's minimum-spanning-tree
    airline length in mils. Shorter airlines on critical nets (switching
    loops, input/output capacitors) mean a better placement; non-critical
    nets (enables, set resistors, feedback dividers) may be lengthened to
    buy routing space. Plane nets like GND have many pads and a meaningless
    airline - judge them by proximity to plane connections instead.

    Args:
        cmp_designators (list, optional): Components whose nets to analyze
            (e.g. ["U12", "R42"]). Omit to use the current Altium selection.
        max_pads_per_net (int): Nets with more pads than this (e.g. GND)
            return only pads belonging to the given components, plus the
            total pad_count. Their airline is also skipped. Default 40.

    Returns:
        str: JSON object with one entry per net: pad_count,
             airline_mst_mils (None for large nets), and pads
             [{designator, pin, x, y}, ...] in mils relative to the board
             origin. Large nets set pads_truncated=true.
    """
    logger.info(f"Getting net connections (designators={cmp_designators})")

    params = {}
    if cmp_designators:
        params["designators"] = cmp_designators

    response = await altium_bridge.execute_command("get_net_connections", params)

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting net connections: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to get net connections: {error_msg}"})

    result = response.get("result", {})
    if isinstance(result, str):
        result = json.loads(result)

    target_set = set(cmp_designators or result.get("targets", []))

    # Group the flat pad list by net and compute airline lengths
    nets = {}
    for pad in result.get("pads", []):
        nets.setdefault(pad["net"], []).append(pad)

    out_nets = []
    for name in result.get("net_names", []):
        pads = nets.get(name, [])
        entry = {"net": name, "pad_count": len(pads)}
        if len(pads) <= max_pads_per_net:
            entry["airline_mst_mils"] = round(_mst_length([(p["x"], p["y"]) for p in pads]), 1)
            entry["pads"] = [
                {"designator": p["designator"], "pin": p["pin"], "x": p["x"], "y": p["y"]}
                for p in pads
            ]
        else:
            entry["airline_mst_mils"] = None
            entry["pads_truncated"] = True
            entry["pads"] = [
                {"designator": p["designator"], "pin": p["pin"], "x": p["x"], "y": p["y"]}
                for p in pads
                if not target_set or p["designator"] in target_set
            ]
        out_nets.append(entry)

    out_nets.sort(key=lambda e: (e["airline_mst_mils"] is None, -(e["airline_mst_mils"] or 0)))

    logger.info(f"Net connections: {len(out_nets)} nets")
    return json.dumps({"net_count": len(out_nets), "nets": out_nets}, indent=2)

def _rotate_offset(dx: float, dy: float, degrees: float) -> tuple:
    """Rotate a rotation-0 pad offset counterclockwise by the given angle."""
    import math
    rad = math.radians(degrees)
    return (dx * math.cos(rad) - dy * math.sin(rad),
            dx * math.sin(rad) + dy * math.cos(rad))

@mcp.tool()
async def check_orientation(ctx: Context, cmp_designators: list = None, min_improvement_mils: float = 25) -> str:
    """
    Advisory check: find 2-pad passives whose rotation could be improved.

    For each 2-pad component in the target set, every orthogonal rotation
    (0/90/180/270) is scored in place, summing one term per pad:
    - routable nets (few pads): the net's total airline (MST) length with
      this pad at the candidate position - so a pad that merely slides along
      a pass-through flow (e.g. a bulk cap in a power chain) scores as
      nearly free, while a genuine detour costs its real length
    - plane nets (many pads, e.g. GND): distance to the nearest same-net
      pad - a capacitor's ground-return loop is local, so proximity to the
      IC's GND/thermal pad is what matters
    A component is reported when a different rotation beats the current one
    by at least min_improvement_mils.

    This is advisory, not pass/fail: it measures geometry only and knows
    nothing about net criticality. Suggestions matter for loop-critical
    parts (capacitor ground returns, snubbers, input/output filters) and
    should usually be ignored for parts whose orientation is electrically
    arbitrary (pull-ups, strapping resistors, enables) or where a datasheet
    layout recommendation says otherwise. Weigh suggestions with judgement
    rather than applying them blindly. If a suggestion with
    bbox_changes=true is applied (a 90-degree change alters the body
    outline), re-run check_placement afterwards.

    Args:
        cmp_designators (list, optional): Components to check. Omit to use
            the current Altium selection. Components with more or fewer than
            2 pads are skipped.
        min_improvement_mils (float): Only report components where the best
            rotation improves the connection score by at least this many
            mils (default 25).

    Returns:
        str: JSON object with checked_count and suggestions, each having
             designator, current_rotation, suggested_rotation,
             improvement_mils, bbox_changes, and a per-pad breakdown of
             nearest same-net distances at the current vs suggested rotation.
    """
    import math

    logger.info(f"Checking orientation (designators={cmp_designators})")

    # Resolve the target designators from the selection when not given
    if not cmp_designators:
        sel_resp = await altium_bridge.execute_command("get_selected_components_coordinates", {})
        if not sel_resp.get("success", False):
            return json.dumps({"success": False, "error": sel_resp.get("error", "Unknown error")})
        sel = sel_resp.get("result", [])
        if isinstance(sel, str):
            sel = json.loads(sel)
        cmp_designators = [c["designator"] for c in sel if "designator" in c]
        if not cmp_designators:
            return json.dumps({"success": False, "error": "No components selected and no designators given"})

    pins_resp = await altium_bridge.execute_command("get_component_pins", {"designators": cmp_designators})
    if not pins_resp.get("success", False):
        return json.dumps({"success": False, "error": pins_resp.get("error", "Unknown error")})
    comps = pins_resp.get("result", [])
    if isinstance(comps, str):
        comps = json.loads(comps)

    nets_resp = await altium_bridge.execute_command("get_net_connections", {"designators": cmp_designators})
    if not nets_resp.get("success", False):
        return json.dumps({"success": False, "error": nets_resp.get("error", "Unknown error")})
    net_data = nets_resp.get("result", {})
    if isinstance(net_data, str):
        net_data = json.loads(net_data)

    # net name -> list of (designator, x, y) for every pad on the net
    net_pads = {}
    for p in net_data.get("pads", []):
        net_pads.setdefault(p["net"], []).append((p["designator"], p["x"], p["y"]))

    PLANE_NET_PAD_COUNT = 40  # nets above this are treated as planes

    suggestions = []
    checked = 0
    for comp in comps:
        pins = comp.get("pins", [])
        if len(pins) != 2 or "x" not in comp:
            continue
        mirror = comp.get("layer") == "Bottom Layer"

        def score(rotation):
            """Hybrid per-pad score (see docstring); None if no pad scores."""
            total, detail = 0.0, []
            for pin in pins:
                net = pin.get("net", "")
                cands = [(x, y) for (d, x, y) in net_pads.get(net, [])
                         if d != comp["designator"]] if net else []
                if not cands:
                    continue
                dx = -pin["dx"] if mirror else pin["dx"]
                ox, oy = _rotate_offset(dx, pin["dy"], rotation)
                px, py = comp["x"] + ox, comp["y"] + oy
                if len(cands) > PLANE_NET_PAD_COUNT:
                    value = min(math.dist((px, py), c) for c in cands)
                    metric = "nearest_return_mils"
                else:
                    value = _mst_length(cands + [(px, py)])
                    metric = "net_airline_mils"
                total += value
                detail.append({"pin": pin["name"], "net": net, metric: round(value, 1)})
            return (total, detail) if detail else None

        current = comp.get("rotation", 0) % 360
        cur = score(current)
        if cur is None:
            continue
        checked += 1

        best_rot, best = current, cur
        for r in (0, 90, 180, 270):
            s = score(r)
            if s is not None and s[0] < best[0]:
                best_rot, best = r, s

        improvement = cur[0] - best[0]
        if best_rot != current and improvement >= min_improvement_mils:
            suggestions.append({
                "designator": comp["designator"],
                "current_rotation": current,
                "suggested_rotation": best_rot,
                "improvement_mils": round(improvement, 1),
                "bbox_changes": (best_rot - current) % 180 != 0,
                "current_pads": cur[1],
                "suggested_pads": best[1],
            })

    suggestions.sort(key=lambda s: -s["improvement_mils"])
    logger.info(f"Orientation check: {len(suggestions)} suggestions from {checked} components")
    return json.dumps({
        "checked_count": checked,
        "min_improvement_mils": min_improvement_mils,
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    }, indent=2)

@mcp.tool()
async def check_placement(ctx: Context, cmp_designators: list = None, clearance_mils: float = 6) -> str:
    """
    Verify component placement: find overlaps and clearance violations.

    Checks each target component against every other component on the same
    side of the board. Bounding boxes (which include silkscreen) are used as
    a fast prefilter; close pairs are then measured precisely with Altium's
    primitive-to-primitive distance, so reported distances are true minimum
    distances between any primitives (pads, silk, etc.) of the two parts.

    Run this after placing components - a screenshot is not verification.

    Args:
        cmp_designators (list, optional): Components to check (e.g. ["U12", "R42"]).
            Omit to check the components currently selected in Altium.
        clearance_mils (float): Minimum allowed primitive-to-primitive distance
            in mils (default 6). Pairs closer than this are reported.

    Returns:
        str: JSON object with checked_count, violation_count, and a violations
             list. Each violation has a/b designators, type
             ("bounding_box_overlap" = the parts' outlines intersect, or
             "clearance" = distance below threshold), distance_mils (0 =
             touching/overlapping copper or silk), overlap sizes when boxes
             intersect, and the other part's x/y position. An empty violations
             list means the placement is clean at the given clearance.
    """
    logger.info(f"Checking placement (designators={cmp_designators}, clearance={clearance_mils})")

    params = {"clearance_mils": clearance_mils}
    if cmp_designators:
        params["designators"] = cmp_designators

    response = await altium_bridge.execute_command("check_placement", params)

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error checking placement: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to check placement: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Placement check complete: {result.get('violation_count', '?')} violations")
    return json.dumps(result, indent=2)

# Board snapshot for the silkscreen tools. An export takes a few seconds, so
# views and option queries reuse it. Moves made through
# set_designator_positions are written into it, check_silkscreen always
# refreshes it, and it is re-exported once older than SILK_SNAPSHOT_SECONDS.
# Edits made by hand in Altium are seen after that, or with refresh=True.
SILK_SNAPSHOT_SECONDS = 900
_silk_snapshot = {"board": None, "time": 0.0}

async def _silk_board(refresh: bool = False):
    """The board's silkscreen geometry, exported from Altium when needed."""
    snap = _silk_snapshot
    if refresh or snap["board"] is None or time.time() - snap["time"] > SILK_SNAPSHOT_SECONDS:
        response = await altium_bridge.execute_command("export_silkscreen_data", {})
        if not response.get("success", False):
            raise RuntimeError(response.get("error", "Unknown error"))
        result = response.get("result", {})
        if isinstance(result, str):
            result = json.loads(result)
        text = Path(result["file"]).read_text(encoding="utf-8-sig", errors="replace")
        snap["board"], snap["time"] = silkscreen.Board(text), time.time()
    return snap["board"]

async def _check_silk_drc(designators: list = None) -> dict:
    params = {"designators": designators} if designators else {}
    response = await altium_bridge.execute_command("check_silkscreen", params)
    if not response.get("success", False):
        raise RuntimeError(response.get("error", "Unknown error"))
    result = response.get("result", {})
    return json.loads(result) if isinstance(result, str) else result

def _group_silk_violations(drc: dict) -> dict:
    """Altium's per-object silk violations as {designator: [description]}."""
    grouped = {}
    for v in drc.get("violations", []):
        what = v.get("object", "")
        if v.get("owner"):
            what += f" {v['owner']}" + (f"-{v['detail']}" if v.get("object") == "Pad" and v.get("detail") else "")
        elif v.get("detail"):
            what += f" '{v['detail']}'"
        if "distance_mils" in v:
            what += f" ({v['distance_mils']} mil)"
        grouped.setdefault(v["designator"], []).append(f"{v['rule']}: {what}")
    return dict(sorted(grouped.items()))

def _silk_side(side, designators, board) -> str:
    """'T' or 'B' from a side argument, else from the first designator."""
    if side:
        s = str(side).strip().lower()
        if s in ("top", "t"):
            return "T"
        if s in ("bottom", "b", "bot"):
            return "B"
        raise ValueError("side must be 'top' or 'bottom'")
    for d in designators or ():
        c = board.components.get(d)
        if c is not None and c.side in ("T", "B"):
            return c.side
    return "T"

def _current_boxes(board, side=None) -> dict:
    return {d: (c.text_box, c.text_rotation) for d, c in board.components.items()
            if c.visible and c.side in ("T", "B") and (side is None or c.side == side)}

def _focus_around(board, designators, side, min_size=300.0):
    """Area showing the parts, their current labels, and some surroundings."""
    bb = silkscreen.focus_box(board, designators, side)
    if bb is None:
        return None
    for d in designators:
        c = board.components.get(d)
        if c is not None and c.side == side and c.visible:
            bb = silkscreen._union(bb, c.text_box)
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    hw, hh = max((bb[2] - bb[0]) / 2, min_size / 2), max((bb[3] - bb[1]) / 2, min_size / 2)
    return (cx - hw, cy - hh, cx + hw, cy + hh)

def _box_info(rect, rotation) -> dict:
    return {"x": round((rect[0] + rect[2]) / 2, 2), "y": round((rect[1] + rect[3]) / 2, 2),
            "rotation": round(rotation) % 360,
            "width": round(rect[2] - rect[0], 1), "height": round(rect[3] - rect[1], 1)}

SILK_VIEW_LEGEND = ("grid lines labelled in mils (board origin); copper = solder mask openings "
                    "(dark = vias); white = silk; grey names = part centres; yellow = designators; "
                    "red = designators with problems; cyan = the designators asked about, with a line "
                    "to their part; green numbered = options")

AUTOPLACE_POSITIONS = ["TopCenter", "CenterRight", "BottomCenter", "CenterLeft",
                       "TopLeft", "TopRight", "BottomLeft", "BottomRight"]
AUTOPLACE_ROTATIONS = {"component": 0, "horizontal": 1, "along_side": 2,
                       "along_axis": 3, "along_pins": 4, "klc": 5}
# The script's GUI defaults, used when there is no saved settings file
AUTOPLACE_DEFAULTS = {
    "failed_action": "center", "avoid_vias": True, "rotation_strategy": 5,
    "try_altered_rotation": True, "second_pass": True, "unhide_all": False,
    "auto_hide": [], "text_height_mils": 31.496, "stroke_width_mils": 5.906,
    "position_delta_mils": 16.535, "positions": AUTOPLACE_POSITIONS[:4],
}

def _to_mils(text: str):
    """'30mil', '0.8mm', '0.05in' or a bare number (mils) -> mils."""
    m = re.match(r"\s*([-+]?\d*\.?\d+)\s*(mil|mm|in)?\s*$", str(text), re.I)
    if not m:
        return None
    value, unit = float(m.group(1)), (m.group(2) or "mil").lower()
    return value * {"mil": 1.0, "mm": 1000 / 25.4, "in": 1000.0}[unit]

def _saved_autoplacer_settings() -> dict:
    """Options saved by the AutoPlaceSilkscreen GUI (AutoPlaceSilkscreen.ini in
    Altium's application data folder), in this tool's terms. Empty if none."""
    import configparser
    files = glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Altium", "*", "AutoPlaceSilkscreen.ini"))
    if not files:
        return {}
    ini = configparser.ConfigParser()
    try:
        ini.read(max(files, key=os.path.getmtime))
        g = ini["General"]
    except (configparser.Error, KeyError):
        return {}
    flag = lambda key, default: g.get(key, "1" if default else "0").strip() in ("1", "true", "True")
    out = {
        "failed_action": {"0": "center", "1": "hide", "2": "restore"}.get(g.get("FailedPlacementOptions", "0"), "center"),
        "avoid_vias": flag("AvoidVias", True),
        "rotation_strategy": int(g.get("RotationStrategy", "5")),
        "try_altered_rotation": flag("TryAlteredRotation", True),
        "second_pass": flag("WiggleEnabled", True),
        "unhide_all": flag("UnhideAllDesignators", False),
        "auto_hide": [x.strip() for x in g.get("AutoHideList", "").split(",") if x.strip()]
                     if flag("AutoHideEnabled", False) else [],
        "text_height_mils": (_to_mils(g.get("FixedSize", "")) or 0) if flag("FixedSizeEnabled", True) else 0,
        "stroke_width_mils": (_to_mils(g.get("FixedWidth", "")) or 0) if flag("FixedWidthEnabled", True) else 0,
        "position_delta_mils": _to_mils(g.get("PositionDelta", "")) or AUTOPLACE_DEFAULTS["position_delta_mils"],
        "positions": [p for i, p in enumerate(AUTOPLACE_POSITIONS, 1) if flag(f"Position{i}", i <= 4)],
    }
    return out

@mcp.tool()
async def auto_place_silkscreen(ctx: Context, designators: list = None, selected_only: bool = False,
                                failed_action: str = None, avoid_vias: bool = None,
                                rotation_strategy: str = None, positions: list = None,
                                try_altered_rotation: bool = None, second_pass: bool = None,
                                unhide_all: bool = None, auto_hide: list = None,
                                allow_under: list = None, text_height_mils: float = None,
                                stroke_width_mils: float = None, position_delta_mils: float = None,
                                component_outline_layer: str = None,
                                use_saved_settings: bool = True) -> str:
    """
    Bulk-place designators with the Silkscreen Auto Placer script - step 1 of
    silkscreen cleanup. Then finish by hand with the other silkscreen tools.

    Runs a headless port of the AutoPlaceSilkscreen script inside Altium. For
    every part, smallest first, it tries the enabled autopositions on an
    offset grid with shrinking text, then a retry pass (rotation flip, wider
    grid) and a 2nd-pass "wiggle" search up to 100 mil out. It avoids pads,
    component bodies, other silk and (optionally) vias, and keeps the Board
    Outline Clearance rule's distance from the board edge. Typically 80-95%
    of designators end up placed; the rest are handled per failed_action.
    Everything is one undo step. A whole-board run moves every designator:
    ask the user to save the board first.

    It is fast and good at the bulk, but it does not judge readability:
    labels can land far from their part (2nd-pass label blocks), next to a
    neighbour, or over open vias when avoid_vias is off. Always follow up:
    1. check_silkscreen - lists what needs work (failed designators, DRC,
       ambiguous or distant labels).
    2. view_silkscreen / get_designator_options - look at each problem area
       and find legal spots.
    3. set_designator_positions - place them (dry_run first for neighbours).

    Options default to the settings last saved by the script's GUI
    (AutoPlaceSilkscreen.ini) when use_saved_settings is on, else the GUI's
    defaults. Any argument given here overrides them.

    Args:
        designators (list, optional): Only place these designators. Omit for
            the whole board (or the selection with selected_only).
        selected_only (bool): Place only the components selected in Altium.
        failed_action (str): What to do with designators that could not be
            placed: "center" (centre them on their part, where
            check_silkscreen flags them), "hide", or "restore" (put them back
            where they were).
        avoid_vias (bool): Treat vias as obstacles.
        rotation_strategy (str): "component", "horizontal", "along_side",
            "along_axis", "along_pins" or "klc" (KLC style).
        positions (list): Autopositions to try, in order: TopCenter,
            CenterRight, BottomCenter, CenterLeft, TopLeft, TopRight,
            BottomLeft, BottomRight.
        try_altered_rotation (bool): Also try the text turned 90 degrees.
        second_pass (bool): Run the wider "wiggle" search for failures.
        unhide_all (bool): Show hidden designators first so they get placed.
        auto_hide (list): Designator prefixes to hide instead of placing,
            e.g. ["TP", "MT", "FID"]; [] hides none.
        allow_under (list): Designators whose bodies silk may cover.
        text_height_mils (float): Fixed text height; 0 = size from the part
            (shrinks to 25 mil when needed).
        stroke_width_mils (float): Fixed stroke width; 0 = from the height.
        position_delta_mils (float): Extra gap between autoposition and part.
        component_outline_layer (str): Mechanical layer holding component
            bodies (default "Mechanical 13" or a "Component Outline" layer).
        use_saved_settings (bool): Start from the GUI's saved settings.

    Returns:
        str: JSON with placed/failed counts per pass, the failed designators,
             the settings used and the next step.
    """
    settings = dict(AUTOPLACE_DEFAULTS)
    source = "script defaults"
    if use_saved_settings:
        saved = _saved_autoplacer_settings()
        if saved:
            settings.update(saved)
            source = "saved GUI settings (AutoPlaceSilkscreen.ini)"
    overrides = {"failed_action": failed_action, "avoid_vias": avoid_vias,
                 "try_altered_rotation": try_altered_rotation, "second_pass": second_pass,
                 "unhide_all": unhide_all, "auto_hide": auto_hide,
                 "text_height_mils": text_height_mils, "stroke_width_mils": stroke_width_mils,
                 "position_delta_mils": position_delta_mils, "positions": positions}
    settings.update({k: v for k, v in overrides.items() if v is not None})
    if rotation_strategy is not None:
        key = str(rotation_strategy).strip().lower().replace(" ", "_")
        if key.isdigit() and 0 <= int(key) <= 5:
            settings["rotation_strategy"] = int(key)
        elif key in AUTOPLACE_ROTATIONS:
            settings["rotation_strategy"] = AUTOPLACE_ROTATIONS[key]
        else:
            return json.dumps({"success": False, "error": f"rotation_strategy must be one of {list(AUTOPLACE_ROTATIONS)}"})
    if settings["failed_action"] not in ("center", "hide", "restore"):
        return json.dumps({"success": False, "error": "failed_action must be center, hide or restore"})
    order = {p.lower(): p for p in AUTOPLACE_POSITIONS}
    try:
        settings["positions"] = [order[str(p).strip().lower()] for p in settings["positions"]]
    except KeyError as e:
        return json.dumps({"success": False, "error": f"unknown position {e}; use {AUTOPLACE_POSITIONS}"})
    if not settings["positions"]:
        return json.dumps({"success": False, "error": "enable at least one position"})

    params = {k: v for k, v in settings.items() if k not in ("auto_hide", "positions",
                                                             "text_height_mils", "stroke_width_mils")}
    params.update({
        "positions": settings["positions"],
        "fixed_size_mils": float(settings["text_height_mils"] or 0),
        "fixed_width_mils": float(settings["stroke_width_mils"] or 0),
        "selected_only": bool(selected_only),
    })
    # Arrays are only sent when non-empty: the script reads '"key": []' as absent
    for key, value in (("designators", designators), ("auto_hide", settings["auto_hide"]),
                       ("allow_under", allow_under)):
        if value:
            params[key] = [str(v) for v in value]
    if component_outline_layer:
        params["outline_layer"] = component_outline_layer

    logger.info(f"auto_place_silkscreen ({source}): {params}")
    response = await altium_bridge.execute_command("auto_place_silkscreen", params, timeout=1800)
    _silk_snapshot["board"] = None      # every designator may have moved
    if not response.get("success", False):
        return json.dumps({"success": False, "error": f"Auto placer failed: {response.get('error', 'Unknown error')}"})
    result = response.get("result", {})
    if isinstance(result, str):
        result = json.loads(result)
    if not result.get("outline_layer_found"):
        result["warning"] = ("no component outline layer found - component bodies were not treated "
                             "as obstacles; pass component_outline_layer")
    result.pop("outline_layer_found", None)
    names = {v: k for k, v in AUTOPLACE_ROTATIONS.items()}
    result["settings"] = dict(settings, rotation_strategy=names[settings["rotation_strategy"]], source=source)
    result["next"] = ("Run check_silkscreen, then fix what it lists with view_silkscreen, "
                      "get_designator_options and set_designator_positions.")
    return json.dumps(result, indent=2)

@mcp.tool()
async def check_silkscreen(ctx: Context, designators: list = None, preview: bool = False):
    """
    Find the designators that need work. Start and finish silkscreen cleanup
    with this.

    Two parts:
    - drc: each designator is tested with Altium's own Silk To Silk
      Clearance, Silk To Solder Mask Clearance and Board Outline Clearance
      rules (Rule.ActualCheck, the test a batch DRC runs) against nearby
      silk, pads, vias, mask objects and the board edge. Ground truth for
      fabrication.
    - quality: problems DRC does not flag - text far from its part or closer
      to another part of the same type, over another part's body or under its
      own (e.g. left on top of its part by an auto-placer), off the board, or
      rotated to read upside down.
    needs_attention lists every designator with either kind of problem;
    hidden lists designators whose display is turned off.

    Args:
        designators (list, optional): Designators to check. Omit for all.
        preview (bool): Also return a whole-board view with the problem
            designators in red (default False).

    Returns:
        JSON with needs_attention, drc and quality (both grouped by
        designator), hidden, plus optional view image(s).
    """
    try:
        drc = await _check_silk_drc(designators)
        board = await _silk_board(refresh=True)
    except Exception as e:
        return json.dumps({"success": False, "error": f"Failed to check silkscreen: {e}"})

    scope = designators or list(board.components)
    boxes = {d: b for d, b in _current_boxes(board).items() if d in scope}
    quality = {d: p for d, p in silkscreen.evaluate(board, boxes, geometry=False).items() if p}
    # Labels in an intact label block are linked to their parts: distance and
    # "reads as" problems do not apply. A disturbed block is a problem itself.
    blocks = []
    for rec in _load_blocks(board.file):
        broken = label_blocks.check_record(board, rec)
        blocks.append({"parts": rec["members"], "link": rec["link"], "intact": not broken,
                       **({"marker": rec["marker"]} if rec.get("marker") else {}),
                       **({"problems": broken} if broken else {})})
        for d in rec["members"]:
            if d not in scope:
                continue
            if broken:
                quality.setdefault(d, []).append("label block broken: " + "; ".join(broken))
            elif d in quality:
                keep = [p for p in quality[d] if not label_blocks.is_association_problem(p)]
                if keep:
                    quality[d] = keep
                else:
                    del quality[d]
    grouped = _group_silk_violations(drc)
    attention = sorted(set(grouped) | set(quality))
    summary = {
        "board": board.file,
        "checked_count": drc.get("checked_count"),
        "needs_attention_count": len(attention),
        "needs_attention": attention,
        "drc_violation_count": drc.get("violation_count"),
        "drc": grouped,
        "quality": quality,
        "hidden": sorted(d for d in scope if d in board.components and not board.components[d].visible),
    }
    if blocks:
        summary["label_blocks"] = blocks
    if drc.get("missing_designators"):
        summary["missing_designators"] = drc["missing_designators"]
    text = json.dumps(summary, indent=2)
    if not preview:
        return text
    images = []
    for side in ("T", "B"):
        side_boxes = {d: b for d, b in boxes.items() if board.components[d].side == side}
        if side_boxes:
            images.append(MCPImage(data=silkscreen.render_view(
                board, side_boxes, side=side, problems=attention, max_px=1600), format="png"))
    return [text] + images

@mcp.tool()
async def view_silkscreen(ctx: Context, designators: list = None, region: list = None,
                          side: str = None, grid_mils: float = 0, refresh: bool = False):
    """
    Look at the silkscreen around some designators or in a region.

    Renders one board side cleanly - no Altium UI, no copper clutter - with a
    grid labelled in mils (board-origin coordinates, the same ones
    set_designator_positions takes), so a free spot can be read straight off
    the image. Shows solder mask openings, silk, part names at their centres,
    every designator box, and marks designators whose current spot has a
    problem in red. The designators asked about are cyan with a line to
    their part.

    Uses the board snapshot (refreshed by check_silkscreen, updated by
    set_designator_positions, at most 5 minutes old). Pass refresh=True after
    editing the board by hand in Altium.

    Args:
        designators (list, optional): Zoom to these parts and highlight them.
        region (list, optional): [x_min, y_min, x_max, y_max] in mils to show
            instead. Omit both for the whole board.
        side (str, optional): "top" or "bottom". Default: the side of the
            first designator, else top.
        grid_mils (float): Grid spacing; 0 picks one to suit the zoom.
        refresh (bool): Re-export the board from Altium first.

    Returns:
        JSON (view bounds, grid, legend, problems of the designators in view)
        plus the image.
    """
    try:
        board = await _silk_board(refresh)
        s = _silk_side(side, designators, board)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

    if region:
        if len(region) != 4:
            return json.dumps({"success": False, "error": "region must be [x_min, y_min, x_max, y_max]"})
        x0, y0, x1, y1 = (float(v) for v in region)
        focus, margin = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), 0.0
    elif designators:
        focus, margin = _focus_around(board, designators, s), 60.0
        if focus is None:
            return json.dumps({"success": False, "error": f"none of {designators} is on the {s} side"})
    else:
        focus, margin = None, 40.0

    boxes = _current_boxes(board, s)
    if focus is not None:
        view = (focus[0] - margin, focus[1] - margin, focus[2] + margin, focus[3] + margin)
        boxes = {d: b for d, b in boxes.items()
                 if not (b[0][2] < view[0] or b[0][0] > view[2] or b[0][3] < view[1] or b[0][1] > view[3])}
    problems = _block_exempt(board, {d: p for d, p in silkscreen.evaluate(board, boxes).items() if p})
    png = silkscreen.render_view(board, _current_boxes(board, s), focus=focus, side=s,
                                 targets=designators or (), problems=problems,
                                 margin=margin, grid=grid_mils or None)
    info = {
        "side": "top" if s == "T" else "bottom",
        "designators_in_view": len(boxes),
        "legend": SILK_VIEW_LEGEND,
        "problems": problems,
    }
    if focus is not None:
        info["view_mils"] = [round(v, 1) for v in view]
    return [json.dumps(info, indent=2), MCPImage(data=png, format="png")]

@mcp.tool()
async def get_designator_options(ctx: Context, designators: list, count: int = 5,
                                 max_gap_mils: float = 50, min_height_mils: float = 0,
                                 refresh: bool = False):
    """
    Legal spots for designators, ranked, drawn as numbered boxes.

    For each designator: its current spot and what is wrong with it, then up
    to `count` distinct legal positions around its part - clear of silk, solder
    mask openings, untented vias, the board edge and every other designator
    where it is now, readable, and nearer its own part than any other part of
    the same type. Ranked by: close to the part, above > left/right > below,
    text along the part's long axis, centred. Pick one (or use the image to
    judge a better spot) and apply it with set_designator_positions using the
    option's x, y and rotation.

    The designators asked about are not obstacles to each other: when placing
    neighbours, check the chosen spots together with
    set_designator_positions(dry_run=True) first. When a designator has no
    legal spot, blocked_by says what is in the way: ask again together with
    the neighbouring designators (so they can move to make room), allow
    smaller text (min_height_mils), or place its row's designators as a
    block with get_label_block_options. Do not hide designators to get rid
    of problems; only mounting holes and fiducials go without one.

    Args:
        designators (list): Designators to find spots for.
        count (int): Options per designator (default 5).
        max_gap_mils (float): Furthest the text may sit from its part's pads
            and silk (default 50).
        min_height_mils (float): Also offer smaller text, down to this height,
            in 5 mil steps (0 = current size only).
        refresh (bool): Re-export the board from Altium first.

    Returns:
        JSON per designator (current spot + problems, options with n, x, y,
        rotation, side, gap_mils and height when reduced, or blocked_by) plus
        an image per board side with the options numbered.
    """
    try:
        board = await _silk_board(refresh)
    except Exception as e:
        return json.dumps({"success": False, "error": f"Failed to export silkscreen data: {e}"})
    scope, skipped = silkscreen.split_scope(board, designators or [])
    if not scope:
        return json.dumps({"success": False, "error": "no placeable designators", "skipped": skipped})

    opts = silkscreen.Options(max_gap=max_gap_mils, min_height=min_height_mils or None)
    found = silkscreen.options_for(board, scope, count=max(1, count), options=opts)
    current = silkscreen.evaluate(board, {d: (board.components[d].text_box,
                                              board.components[d].text_rotation) for d in scope})
    result = {}
    for d in scope:
        c = board.components[d]
        entry = {"current": dict(_box_info(c.text_box, c.text_rotation), problems=current[d])}
        entry["options"] = []
        for i, cand in enumerate(found[d]["options"], 1):
            o = {"n": i, "x": round(cand.center[0], 2), "y": round(cand.center[1], 2),
                 "rotation": round(cand.rotation) % 360,
                 "side": silkscreen.SIDE_NAMES.get(cand.side_name, cand.side_name),
                 "gap_mils": round(silkscreen._rect_rect_dist(cand.rect, c.extent), 1)}
            if cand.note == "reduced height":
                o["height"] = cand.height
                o["stroke_width"] = cand.stroke
            entry["options"].append(o)
        if found[d]["blocked_by"]:
            entry["blocked_by"] = found[d]["blocked_by"]
        result[d] = entry
    out = {"designators": result, "legend": SILK_VIEW_LEGEND}
    if skipped:
        out["skipped"] = skipped
    images = []
    for side in ("T", "B"):
        on_side = [d for d in scope if board.components[d].side == side]
        if not on_side:
            continue
        png = silkscreen.render_view(board, _current_boxes(board, side),
                                     focus=_focus_around(board, on_side, side), margin=60.0, side=side,
                                     targets=on_side, problems=[d for d in on_side if current[d]],
                                     options={d: found[d]["options"] for d in on_side})
        images.append(MCPImage(data=png, format="png"))
    return [json.dumps(out, indent=2)] + images

@mcp.tool()
async def set_designator_positions(ctx: Context, placements: list, dry_run: bool = False,
                                   verify: bool = True) -> str:
    """
    Place designators - by coordinates or relative to their part - and get a
    verdict for each.

    Each placement is either
    - absolute: x, y = centre of the text box in mils (board origin), as read
      off view_silkscreen or taken from get_designator_options, or
    - relative: side = "above" | "below" | "left" | "right" | "inside" of its
      own part, with gap (mils from the part's pads/silk; default just clear
      of them) and offset (slide along that side, +x or +y).
    Optional on both: rotation (0/90 top, 0/270 bottom read correctly;
    default keeps a readable current rotation), height and stroke_width
    (mils), visible (show/hide - hide only mounting holes and fiducials;
    designators with no room go in a label block, see
    get_label_block_options). A placement with only visible (or only
    rotation/height) keeps the current centre.

    dry_run=True changes nothing: every placement is checked against silk,
    mask openings, the board edge, other designators where they are now and
    each other, plus placement quality. Use it to test a set of neighbours
    together before applying.

    Otherwise all moves are one undo step, and with verify (default) each
    moved designator is then checked with Altium's own silk rules and for
    quality. ok=true means clean. The moves only go to the board the
    snapshot was read from: if another PCB has been focused since, nothing
    moves and the error names both boards.

    Args:
        placements (list): Dicts with designator plus x/y or side (gap,
            offset), and optional rotation, height, stroke_width, visible.
        dry_run (bool): Only check the placements.
        verify (bool): After moving, run Altium's silk DRC on the moved
            designators (default True).

    Returns:
        str: JSON with one verdict per designator: final x, y, rotation,
             ok and problems.
    """
    try:
        board = await _silk_board()
    except Exception as e:
        return json.dumps({"success": False, "error": f"Failed to export silkscreen data: {e}"})

    errors, resolved, entries = [], {}, []
    for idx, p in enumerate(placements or []):
        des = str(p.get("designator", "")).strip() if isinstance(p, dict) else ""
        if not des or des not in board.components:
            errors.append(f"placements[{idx}]: unknown designator {des!r}")
            continue
        if "|" in des:
            errors.append(f"placements[{idx}]: designator must not contain '|'")
            continue
        if ("x" in p) != ("y" in p):
            errors.append(f"placements[{idx}]: give both x and y, or neither")
            continue
        try:
            rect, rot, height, stroke = silkscreen.resolve_placement(board, p)
        except (TypeError, ValueError) as e:
            errors.append(f"placements[{idx}]: {e}")
            continue
        visible = p.get("visible")
        resolved[des] = (rect, rot, visible)
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        size = f"{height:g}" if p.get("height") is not None else ""
        width = f"{stroke:g}" if p.get("height") is not None or p.get("stroke_width") is not None else ""
        vis = "" if visible is None else ("1" if visible else "0")
        entries.append(f"{des}|{cx:.3f}|{cy:.3f}|{rot:g}|{size}|{width}|{vis}")
    if errors:
        return json.dumps({"success": False, "error": "; ".join(errors)})
    if not entries:
        return json.dumps({"success": False, "error": "No placements provided"})

    shown = {d: (r, rot) for d, (r, rot, vis) in resolved.items()
             if vis is not False and (vis or board.components[d].visible)}
    if dry_run:
        problems = silkscreen.evaluate(board, shown, hiding=set(resolved) - set(shown))
        verdicts = {d: dict(_box_info(r, rot), ok=not problems.get(d), problems=problems.get(d, []))
                    for d, (r, rot) in shown.items()}
        for d in set(resolved) - set(shown):
            verdicts[d] = {"hidden": True}
        return json.dumps({"dry_run": True, "all_ok": all(v.get("ok", True) for v in verdicts.values()),
                           "designators": verdicts}, indent=2)

    params = {"placements": entries}
    if board.file:
        # Altium refuses the moves if another PCB is focused by now
        params["board"] = board.file.replace("\\", "/")
    response = await altium_bridge.execute_command("place_designators", params)
    if not response.get("success", False):
        return json.dumps({"success": False, "error": f"Failed to move designators: {response.get('error', 'Unknown error')}"})
    result = response.get("result", {})
    if isinstance(result, str):
        result = json.loads(result)

    # Keep the snapshot in step with what Altium reports
    final = {}
    for item in result.get("designators", []):
        c = board.components.get(item["designator"])
        if c is None:
            continue
        cx, cy, w, h = item["cx"], item["cy"], item["width"], item["height"]
        c.text_rotation = item["rotation"]
        c.text_height = item.get("text_height", c.text_height)
        c.stroke = item.get("stroke_width", c.stroke)
        c.text_box = silkscreen.ink_box((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2),
                                        c.stroke, c.truetype)
        c.visible = item.get("visible", c.visible)
        c.autopos = 0
        final[c.designator] = c

    verdicts = {}
    moved = sorted(d for d, c in final.items() if c.visible)
    grouped = {}
    if verify and moved:
        try:
            grouped = _group_silk_violations(await _check_silk_drc(moved))
        except Exception as e:
            grouped = {d: [f"DRC check failed: {e}"] for d in moved}
    quality = silkscreen.evaluate(board, {d: (final[d].text_box, final[d].text_rotation) for d in moved},
                                  geometry=False)
    for d, c in sorted(final.items()):
        if not c.visible:
            verdicts[d] = {"hidden": True}
            continue
        problems = grouped.get(d, []) + quality.get(d, [])
        verdicts[d] = dict(_box_info(c.text_box, c.text_rotation), ok=not problems, problems=problems)
        if c.text_height:
            verdicts[d]["text_height"] = c.text_height
    out = {"moved_count": len(final), "verified": bool(verify),
           "all_ok": all(v.get("ok", True) for v in verdicts.values()), "designators": verdicts}
    if result.get("missing_designators"):
        out["missing_designators"] = result["missing_designators"]
    return json.dumps(out, indent=2)

# Label blocks applied with place_label_block, per board file, so that
# check_silkscreen can tell a block's labels from labels that wandered off.
# Kept in the exchange folder, never next to the board.
SILK_BLOCKS_FILE = EXCHANGE_DIR / "silk_label_blocks.json"
_block_options_cache = {}

def _load_blocks(board_file: str) -> list:
    try:
        data = json.loads(SILK_BLOCKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get(board_file.lower(), [])

def _save_blocks(board_file: str, records: list):
    try:
        data = json.loads(SILK_BLOCKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data[board_file.lower()] = records
    SILK_BLOCKS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")

def _update_snapshot_graphics(removed_records: list, added_option):
    """Keep the board snapshot in step with link graphics just drawn or
    deleted, instead of exporting the whole board again (every export leaks
    memory in Altium's script engine)."""
    board = _silk_snapshot["board"]
    if board is None:
        return
    for r in removed_records:
        label_blocks.drop_graphics(board, r["side"], r["graphics"])
    if added_option is not None and added_option.graphics:
        board._parse("\n".join(label_blocks.graphics_lines(added_option.side, added_option.graphics)))

def _replaced_graphics(records: list, members: list) -> list:
    """Link graphics of earlier blocks of these parts (placing replaces them)."""
    return [g for r in records if set(r["members"]) & set(members) for g in r["graphics"]]

def _block_members(board, designators: list, whole_row: bool = True) -> list:
    """The parts of a block: one designator stands for its whole row unless
    whole_row is off."""
    names = [str(d).strip() for d in designators or []]
    missing = [d for d in names if d not in board.components]
    if missing:
        raise ValueError(f"not on the board: {missing}")
    if len(names) == 1 and whole_row:
        return label_blocks.find_row(board, names[0])[1]
    return names

def _block_exempt(board, problems: dict) -> dict:
    """problems without the distance and "reads as" problems of labels in
    intact label blocks (their link answers those)."""
    out = dict(problems)
    for rec in _load_blocks(board.file):
        if label_blocks.check_record(board, rec):
            continue
        for d in rec["members"]:
            if d in out:
                keep = [p for p in out[d] if not label_blocks.is_association_problem(p)]
                if keep:
                    out[d] = keep
                else:
                    del out[d]
    return out

def _block_image(board, option) -> bytes:
    side = option.side
    boxes = {d: b for d, b in _current_boxes(board, side).items() if d not in option.members}
    for d, (rect, rot, _, _) in option.labels.items():
        boxes[d] = (rect, rot)
    focus = label_blocks._bounds(
        [label_blocks._bounds(board.components[d].extent for d in option.members), option.rect] +
        [r for g in option.graphics for r in label_blocks._graphic_rects(g)])
    return silkscreen.render_view(board, boxes, focus=focus, side=side, targets=option.members,
                                  margin=60.0, graphics=option.graphics)

@mcp.tool()
async def get_label_block_options(ctx: Context, designators: list, count: int = 3,
                                  min_height_mils: float = 25, max_distance_mils: float = 500,
                                  link: str = "auto", whole_row: bool = True, refresh: bool = False):
    """
    Place a row of parts' designators together as a block, when they do not
    fit next to their parts one by one. Use this instead of hiding
    designators (only mounting holes and fiducials go without one).

    Parts in a row get their designators in a row too, in the same order,
    even some way off. Give one designator to use the whole row it belongs
    to (parts side by side along x or y, similar size across the row, gaps
    up to 50 mil), or list the parts yourself. For a block of just one part
    (its label alone, linked to it), give it with whole_row=False.

    Each option is a block of labels - across the row (in line with each
    part when the pitch allows, else side by side) or end to end along it -
    clear of silk, mask openings, the board edge, other designators and part
    bodies, plus a link when it is not right against the row:
    - leader: a silk line with an arrowhead from the block to the parts;
    - index: matching markers - a capital letter in a circle (then square,
      then triangle) - next to the block and next to the parts.
    Ranked: no link, then leader, then index; nearer and larger text first.
    Apply one with place_label_block.

    Args:
        designators (list): One designator (its row is used) or the block's parts.
        count (int): Options to return (default 3).
        min_height_mils (float): Smallest text allowed (default 25).
        max_distance_mils (float): Furthest the block may sit from the parts (default 500).
        link (str): "auto" (cheapest), "none", "leader" or "index".
        whole_row (bool): With one designator, use its whole row (default)
            or just that part.
        refresh (bool): Re-export the board from Altium first.

    Returns:
        JSON with the parts in order and the numbered options, plus an image
        per option (block labels cyan with lines to their parts, link magenta).
    """
    if link not in ("auto", "none", "leader", "index"):
        return json.dumps({"success": False, "error": "link must be auto, none, leader or index"})
    try:
        board = await _silk_board(refresh)
        members = _block_members(board, designators, whole_row)
        records = _load_blocks(board.file)
        used = label_blocks.used_markers(board, records)
        axis, members, options = label_blocks.block_options(
            board, members, count=max(1, count), min_height=min_height_mils,
            max_distance=max_distance_mils, link=link, used_markers=used,
            ignore=_replaced_graphics(records, members))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    _block_options_cache[(board.file.lower(), tuple(members))] = {
        "time": _silk_snapshot["time"], "options": options,
        "params": dict(min_height=min_height_mils, max_distance=max_distance_mils, link=link)}
    out = {"parts": members, "row_axis": axis,
           "options": [o.to_dict(i) for i, o in enumerate(options, 1)]}
    if not options:
        out["note"] = ("no block fits; allow a longer max_distance_mils or smaller text, or move "
                       "other designators out of the way first")
    return [json.dumps(out, indent=2)] + [MCPImage(data=_block_image(board, o), format="png") for o in options]

@mcp.tool()
async def place_label_block(ctx: Context, designators: list, option: int = 1,
                            whole_row: bool = True) -> str:
    """
    Apply a label block from get_label_block_options: move the parts'
    designators into the block (shown, at the block's text size) and draw
    its link on the silkscreen, then check both with Altium's rules. An
    earlier block of any of these parts is replaced (its link removed).

    Args:
        designators (list): The same designators given to get_label_block_options.
        option (int): Which option (default 1).
        whole_row (bool): As given to get_label_block_options.

    Returns:
        JSON: the block's parts, link and marker, ok, and any Altium
        violations of the labels or the link graphics.
    """
    try:
        board = await _silk_board()
        members = _block_members(board, designators, whole_row)
        axis = label_blocks.row_axis(board, members)
        members = sorted(members, key=lambda d: label_blocks._reading_key(board.components[d], axis))
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    records = _load_blocks(board.file)
    key = (board.file.lower(), tuple(members))
    cached = _block_options_cache.get(key)
    note = None
    if cached is None or cached["time"] != _silk_snapshot["time"]:
        params = cached["params"] if cached else {}
        _, _, options = label_blocks.block_options(
            board, members, count=max(3, option), used_markers=label_blocks.used_markers(board, records),
            ignore=_replaced_graphics(records, members), **params)
        note = "board changed since the options were listed - recomputed them"
    else:
        options = cached["options"]
    if not 1 <= option <= len(options):
        return json.dumps({"success": False, "error": f"option {option} does not exist ({len(options)} options)",
                           "note": note})
    opt = options[option - 1]

    old = [r for r in records if set(r["members"]) & set(members)]
    items = []
    for r in old:
        items += label_blocks.to_items(r["side"], r["graphics"], "-")
    items += label_blocks.to_items(opt.side, opt.graphics, "+")

    moved = json.loads(await set_designator_positions(ctx, opt.placements(), verify=False))
    if "designators" not in moved:
        return json.dumps({"success": False, "error": moved.get("error", "moving the labels failed")})
    graphic_problems = []
    if items:
        response = await altium_bridge.execute_command(
            "edit_silk_graphics", {"items": items, "board": board.file.replace("\\", "/")})
        if not response.get("success", False):
            _silk_snapshot["board"] = None
            return json.dumps({"success": False, "error": f"labels moved, but drawing the link failed: "
                                                          f"{response.get('error', 'Unknown error')}"})
        result = response.get("result", {})
        if isinstance(result, str):
            result = json.loads(result)
        graphic_problems = [f"{v['rule']}: {v['object']} {v.get('owner', '')} {v.get('detail', '')}".strip()
                            for v in result.get("violations", [])]
        if result.get("not_found"):
            graphic_problems.append(f"old link graphics not found: {result['not_found']}")
        _update_snapshot_graphics(old, opt)
    try:
        label_problems = _group_silk_violations(await _check_silk_drc(list(members)))
    except Exception as e:
        label_problems = {d: [f"DRC check failed: {e}"] for d in members}

    records = [r for r in records if r not in old] + [label_blocks.record(opt)]
    _save_blocks(board.file, records)
    out = {"parts": members, "summary": opt.summary, "link": opt.link,
           "ok": not label_problems and not graphic_problems,
           "label_problems": label_problems, "link_problems": graphic_problems}
    if opt.marker:
        out["marker"] = {"letter": opt.marker[0], "shape": opt.marker[1]}
    if old:
        out["replaced_blocks"] = [r["members"] for r in old]
    if note:
        out["note"] = note
    return json.dumps(out, indent=2)

@mcp.tool()
async def remove_label_block(ctx: Context, designators: list) -> str:
    """
    Remove the label block of these parts: its link graphics (leader or
    index markers) are deleted; the designators stay where they are, ready
    to be placed again.

    Args:
        designators (list): Any of the block's parts.

    Returns:
        JSON with the removed block's parts and link.
    """
    try:
        board = await _silk_board()
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
    records = _load_blocks(board.file)
    names = {str(d).strip() for d in designators or []}
    old = [r for r in records if names & set(r["members"])]
    if not old:
        return json.dumps({"success": False, "error": f"no label block holds any of {sorted(names)}"})
    items = [i for r in old for i in label_blocks.to_items(r["side"], r["graphics"], "-")]
    if items:
        response = await altium_bridge.execute_command(
            "edit_silk_graphics", {"items": items, "board": board.file.replace("\\", "/")})
        if not response.get("success", False):
            _silk_snapshot["board"] = None
            return json.dumps({"success": False, "error": response.get("error", "Unknown error")})
        _update_snapshot_graphics(old, None)
    _save_blocks(board.file, [r for r in records if r not in old])
    return json.dumps({"removed": [{"parts": r["members"], "link": r["link"]} for r in old]}, indent=2)

@mcp.tool()
async def get_screenshot(ctx: Context, view_type: str = "pcb", zoom_to: list = None):
    """
    Take a screenshot of the Altium window, returned as viewable image content.

    Args:
        view_type (str): Type of view to capture - 'pcb' or 'sch'
        zoom_to (list, optional): List of component designators (e.g. ["U12", "R42"]).
            PCB view only: Altium zooms to the bounding box of these components
            (plus a margin) before the capture, so the components fill the frame.
            Omit to capture at the current zoom level.

    Returns:
        Image content of the captured window plus a JSON metadata text block
        (window title, size, zoomed component count)
    """
    logger.info(f"Taking screenshot of Altium {view_type} window (zoom_to={zoom_to})")

    try:
        # First, execute the Altium command to ensure the right document type
        # is focused, optionally zooming to the requested components
        params = {"view_type": view_type.lower()}
        if zoom_to:
            params["designators"] = zoom_to
        response = await altium_bridge.execute_command(
            "take_view_screenshot",
            params
        )
        
        # Check for success
        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            logger.error(f"Error focusing {view_type} document: {error_msg}")
            return json.dumps({"success": False, "error": f"Failed to focus the correct document type: {error_msg}"})

        # Altium reports which document ended up focused. If it is not the
        # kind that was asked for (typically: no document of that kind is
        # open), say so instead of returning the wrong editor labelled as
        # the requested view.
        focus_info = response.get("result", {})
        focus_info = focus_info if isinstance(focus_info, dict) else {}
        focused_kind = str(focus_info.get("focused_kind", "") or "").upper()
        focused_document = focus_info.get("focused_document", "")
        wanted_kind = {"pcb": "PCB", "sch": "SCH"}.get(view_type.lower())
        if wanted_kind and focused_kind and focused_kind != wanted_kind:
            logger.error(f"Requested {view_type} view but {focused_kind} document is focused")
            return json.dumps({
                "success": False,
                "error": f"Requested a '{view_type}' view but Altium has a {focused_kind} "
                         f"document focused ({focused_document}). Is a "
                         f"{wanted_kind} document open in the active project?",
                "requested_view_type": view_type,
                "focused_kind": focused_kind,
                "focused_document": focused_document})

        # Run the screenshot capture in a separate thread
        import threading
        import queue
        import datetime
        from PIL import Image
        
        result_queue = queue.Queue()
        
        def capture_screenshot_thread():
            try:
                # Find Altium windows
                altium_windows = []
                altium_fallback_windows = []
                
                def collect_altium_windows(hwnd, _):
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd)
                        
                        # First, look for windows with Altium and .PrjPcb in the title
                        if "Altium" in title and ".PrjPcb" in title:
                            altium_windows.append({
                                "handle": hwnd,
                                "title": title,
                                "class_name": win32gui.GetClassName(hwnd),
                                "rect": win32gui.GetWindowRect(hwnd)
                            })
                        # Collect any window with Altium in the title as fallback
                        elif "Altium" in title:
                            altium_fallback_windows.append({
                                "handle": hwnd,
                                "title": title,
                                "class_name": win32gui.GetClassName(hwnd),
                                "rect": win32gui.GetWindowRect(hwnd)
                            })
                    return True
                
                win32gui.EnumWindows(collect_altium_windows, 0)
                
                # If no specific Altium .PrjPcb windows found, use the fallback
                if not altium_windows and altium_fallback_windows:
                    altium_windows = altium_fallback_windows
                
                if not altium_windows:
                    result_queue.put({
                        "success": False, 
                        "error": f"No Altium windows found for {view_type} view"
                    })
                    return
                
                # Altium owns several top-level windows with its name in the
                # title, and while it switches documents a small transient one
                # can come first. Take the largest window that is not
                # minimized: that is the main frame.
                def area(w):
                    l, t, r, b = w["rect"]
                    return max(0, r - l) * max(0, b - t)
                candidates = [w for w in altium_windows if not win32gui.IsIconic(w["handle"])]
                window = max(candidates or altium_windows, key=area)
                hwnd = window["handle"]
                
                # Bring Altium to the front and let it paint. Altium only
                # renders a schematic view once it has actually been shown on
                # screen, so a capture taken while it sits behind another
                # window comes back with a blank canvas. Windows may refuse
                # SetForegroundWindow from a process that neither is nor was
                # started by the foreground process; a synthetic Alt press
                # before the call is the standard unlock and is harmless.
                activated = False
                altium_pid = win32process.GetWindowThreadProcessId(hwnd)[1]

                def altium_is_foreground():
                    # Altium owns several top-level windows; any of them
                    # being foreground means Altium is in front.
                    fg = win32gui.GetForegroundWindow()
                    return bool(fg) and win32process.GetWindowThreadProcessId(fg)[1] == altium_pid

                try:
                    for attempt in range(2):
                        if attempt:
                            import ctypes
                            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                        try:
                            win32gui.SetForegroundWindow(hwnd)
                        except Exception:
                            pass
                        deadline = time.time() + 1.5
                        while time.time() < deadline:
                            time.sleep(0.1)
                            if altium_is_foreground():
                                activated = True
                                break
                        if activated:
                            break
                except Exception as e:
                    logger.warning(f"Could not bring window to foreground: {e}")
                if not activated:
                    logger.warning("Altium is not the foreground window; the capture may show an unpainted view")
                # A freshly switched-to document needs a moment to render.
                time.sleep(1.0)

                # Measure AFTER activation: restoring or switching can change
                # the frame's size.
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                width = right - left
                height = bottom - top
                if width < 200 or height < 150:
                    result_queue.put({"success": False, "error": f"Altium window is too small to capture ({width}x{height}); is it minimized?"})
                    return
                
                # Take screenshot using GDI functions instead of ImageGrab
                try:
                    # Get device context
                    hwndDC = win32gui.GetWindowDC(hwnd)
                    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
                    saveDC = mfcDC.CreateCompatibleDC()
                    
                    # Create a bitmap object
                    saveBitMap = win32ui.CreateBitmap()
                    saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
                    saveDC.SelectObject(saveBitMap)
                    
                    # Copy the screen into the bitmap
                    saveDC.BitBlt((0, 0), (width, height), mfcDC, (0, 0), win32con.SRCCOPY)
                    
                    # Convert the bitmap to an Image
                    bmpinfo = saveBitMap.GetInfo()
                    bmpstr = saveBitMap.GetBitmapBits(True)
                    img = Image.frombuffer(
                        'RGB',
                        (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
                        bmpstr, 'raw', 'BGRX', 0, 1)
                    
                    # Save a local copy of the screenshot for debugging (non-fatal if it fails)
                    try:
                        debug_filename = str(MCP_DIR / f"screenshot_{view_type}.png")
                        img.save(debug_filename)
                        logger.info(f"Saved debug screenshot to {debug_filename}")
                    except Exception as save_error:
                        logger.warning(f"Could not save debug screenshot to {debug_filename}: {save_error}")
                        debug_filename = None  # Clear it since save failed
                    
                    # Clean up GDI resources
                    win32gui.DeleteObject(saveBitMap.GetHandle())
                    saveDC.DeleteDC()
                    mfcDC.DeleteDC()
                    win32gui.ReleaseDC(hwnd, hwndDC)
                    
                    # Convert to base64
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
                    
                    # Put result in queue
                    result_queue.put({
                        "success": True,
                        "width": width,
                        "height": height,
                        "window_title": window["title"],
                        "window_class": window["class_name"],
                        "view_type": view_type,
                        "foreground_confirmed": activated,
                        "image_format": "PNG",
                        "encoding": "base64",
                        "debug_file": debug_filename,
                        "image_data": img_base64
                    })
                    
                except Exception as e:
                    import traceback
                    trace = traceback.format_exc()
                    logger.error(f"GDI screenshot error: {e}\n{trace}")
                    result_queue.put({
                        "success": False, 
                        "error": f"GDI screenshot failed: {str(e)}",
                        "traceback": trace
                    })
                
            except Exception as e:
                import traceback
                result_queue.put({
                    "success": False, 
                    "error": f"Screenshot thread error: {str(e)}",
                    "traceback": traceback.format_exc()
                })
        
        # Start the thread
        thread = threading.Thread(target=capture_screenshot_thread)
        thread.daemon = True
        thread.start()
        
        # Wait for the thread to complete
        thread.join(timeout=10)  # 10 second timeout
        
        if thread.is_alive():
            logger.error("Screenshot thread timed out")
            return json.dumps({"success": False, "error": "Screenshot operation timed out"})
        
        # Get the result from the queue
        if result_queue.empty():
            logger.error("Screenshot thread did not return a result")
            return json.dumps({"success": False, "error": "Screenshot thread did not return a result"})
        
        result = result_queue.get()

        if not result.get("success", False):
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Screenshot error: {error_msg}")
            return json.dumps({"success": False, "error": error_msg})

        logger.info(f"Screenshot taken successfully, size: {result['width']}x{result['height']}")

        # Return the PNG as a proper MCP image content block instead of inline
        # base64 text: raw base64 in the text result exceeds client token
        # limits (a full-window capture is ~300 KB), while image blocks are
        # rendered natively by clients
        image_base64 = result.pop("image_data")
        result.pop("encoding", None)
        zoom_info = response.get("result", {})
        if isinstance(zoom_info, dict) and "zoomed_component_count" in zoom_info:
            result["zoomed_component_count"] = zoom_info["zoomed_component_count"]
        # What was actually captured, as opposed to what was requested
        result["captured_kind"] = focused_kind or None
        result["captured_document"] = focused_document or None
        return [
            json.dumps(result),
            MCPImage(data=base64.b64decode(image_base64), format="png"),
        ]

    except Exception as e:
        logger.error(f"Error in screenshot function: {str(e)}")
        return json.dumps({"success": False, "error": f"Failed to take screenshot: {str(e)}"})
    
@mcp.tool()
async def layout_duplicator(ctx: Context) -> str:
    """
    First step of layout duplication. Selects source components and returns data to match with destination components.
    
    Returns:
        str: JSON object with source and destination component data for matching
    """
    logger.info("Starting layout duplication - selection phase")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "layout_duplicator", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error in layout duplication selection: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to start layout duplication: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", {})
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"success": False, "error": "No component data returned from Altium"})
    
    # Parse the result to check if no source components were selected
    try:
        if isinstance(components_data, str):
            result_json = json.loads(components_data)
            if not result_json.get("success", True):
                logger.info(f"Source component selection issue: {result_json.get('message', 'Unknown issue')}")
                return json.dumps(result_json)
    except Exception as e:
        logger.error(f"Error parsing layout duplicator result: {e}")
    
    logger.info(f"Retrieved layout duplicator component data")
    return json.dumps(components_data, indent=2)

@mcp.tool()
async def layout_duplicator_apply(ctx: Context, source_designators: list, destination_designators: list) -> str:
    """
    Second step of layout duplication. Applies the layout of source components to destination components.
    
    Args:
        source_designators (list): List of source component designators (e.g., ["R1", "C5", "U3"])
        destination_designators (list): List of destination component designators (e.g., ["R10", "C15", "U7"])
    
    Returns:
        str: JSON object with the result of the layout duplication
    """
    logger.info(f"Applying layout duplication from {source_designators} to {destination_designators}")
    
    # Execute the command in Altium to apply layout duplication
    response = await altium_bridge.execute_command(
        "layout_duplicator_apply",
        {
            "source_designators": source_designators,
            "destination_designators": destination_designators
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error applying layout duplication: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to apply layout duplication: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Layout duplication applied successfully")
    return json.dumps(result, indent=2)
    
@mcp.tool()
async def get_pcb_rules(ctx: Context) -> str:
    """
    Get all design rules from the current Altium PCB
    
    Returns:
        str: JSON array of PCB design rules with their properties
    """
    logger.info("Getting PCB design rules")
    
    # Execute the command in Altium to get rule data
    response = await altium_bridge.execute_command(
        "get_pcb_rules",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB rules: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB rules: {error_msg}"})
    
    # Get the rules data
    rules_data = response.get("result", [])
    
    if not rules_data:
        logger.info("No PCB rules found")
        return json.dumps({"message": "No PCB rules found in the current document"})
    
    logger.info(f"Retrieved PCB rules data")
    return json.dumps(rules_data, indent=2)

@mcp.tool()
async def get_pcb_layer_stackup(ctx: Context) -> str:
    """
    Get the detailed layer stackup information from the current Altium PCB including
    copper thickness, dielectric materials, constants, and heights
    
    Returns:
        str: JSON object with detailed layer stackup information
    """
    logger.info("Getting PCB layer stackup information")
    
    # Execute the command in Altium to get layer stackup data
    response = await altium_bridge.execute_command(
        "get_pcb_layer_stackup",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB layer stackup: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB layer stackup: {error_msg}"})
    
    # Get the stackup data
    stackup_data = response.get("result", {})
    
    if not stackup_data:
        logger.info("No PCB layer stackup found")
        return json.dumps({"message": "No PCB layer stackup found in the current document"})
    
    logger.info(f"Retrieved PCB layer stackup data")
    return json.dumps(stackup_data, indent=2)

@mcp.tool()
async def get_output_job_containers(ctx: Context) -> str:
    """
    Get all available output job containers from a specified OutJob file
    
    Args:
        outjob_path (str): Path to the OutJob file (optional, will use first open OutJob if not provided)
    
    Returns:
        str: JSON array with all output job containers and their properties
    """
    logger.info("Getting output job containers from the first open OutJob")
    
    # Execute the command in Altium to get output job containers
    response = await altium_bridge.execute_command(
        "get_output_job_containers", 
        {}  # No parameters needed - will use first open OutJob
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting output job containers: {error_msg}")
        return json.dumps({"error": f"Failed to get output job containers: {error_msg}"})
    
    # Get the containers data
    containers_data = response.get("result", [])
    
    if not containers_data:
        logger.info("No output job containers found")
        return json.dumps({"message": "No output job containers found"})
    
    logger.info(f"Retrieved output job containers data")
    return containers_data  # Already in JSON format

@mcp.tool()
async def run_output_jobs(ctx: Context, container_names: list) -> str:
    """
    Run specified output job containers
    
    Args:
        container_names (list): List of container names to run
    
    Returns:
        str: JSON object with results of running the output jobs
    """
    logger.info(f"Running output jobs")
    logger.info(f"Containers to run: {container_names}")
    
    # Execute the command in Altium to run output jobs
    response = await altium_bridge.execute_command(
        "run_output_jobs", 
        {"container_names": container_names}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error running output jobs: {error_msg}")
        return json.dumps({"error": f"Failed to run output jobs: {error_msg}"})
    
    # Get the result data
    result_data = response.get("result", {})
    
    logger.info(f"Output jobs execution completed")
    
    # If result_data is a string, it's already in JSON format
    if isinstance(result_data, str):
        return result_data
    
    # Otherwise, convert to JSON
    return json.dumps(result_data, indent=2)

@mcp.tool()
async def create_pcb_footprint(ctx: Context, footprint_name: str, description: str, pads: list, courtyard_x_mm: float = 0, courtyard_y_mm: float = 0) -> str:
    """
    Create a new PCB footprint in the currently active PcbLib document.
    The PcbLib (e.g. Discrete.PcbLib) must be the focused document in Altium.

    Pad format: each element is "pad_number|x_mm|y_mm|width_mm|height_mm|shape"
                shape options: Rect (default), Round, Oval
                Coordinates are in mm relative to component origin (0,0).
                Pin 1 is indicated by a gap in the top-left silkscreen corner.
                Pads are created locked.

    Courtyard & silkscreen are auto-generated from pad extents + 0.25 mm margin
    unless courtyard_x_mm / courtyard_y_mm are provided explicitly (half-dimensions).

    Args:
        footprint_name (str): Footprint name as it will appear in the library
        description (str): Description string
        pads (list): List of pad definitions, e.g. ["1|-0.9|0.55|1.0|0.8|Rect", ...]
        courtyard_x_mm (float): Half-width of courtyard in mm (0 = auto)
        courtyard_y_mm (float): Half-height of courtyard in mm (0 = auto)

    Returns:
        str: JSON object with result
    """
    logger.info(f"Creating PCB footprint: {footprint_name} with {len(pads)} pads")

    response = await altium_bridge.execute_command(
        "create_pcb_footprint",
        {
            "footprint_name": footprint_name,
            "description": description,
            "pads": pads,
            "courtyard_x_mm": courtyard_x_mm,
            "courtyard_y_mm": courtyard_y_mm,
        }
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating footprint: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create footprint: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Footprint {footprint_name} created successfully")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_server_status(ctx: Context) -> str:
    """Get the current status of the Altium MCP server"""
    status = {
        "server": "Running",
        "altium_exe": altium_bridge.config.altium_exe_path,
        "script_path": altium_bridge.config.script_path,
        "altium_found": os.path.exists(altium_bridge.config.altium_exe_path),
        "script_found": os.path.exists(altium_bridge.config.script_path),
    }
    
    return json.dumps(status, indent=2)

if __name__ == "__main__":
    logger.info("Starting Altium MCP Server...")
    logger.info(f"Using MCP directory: {MCP_DIR}")
    
    # Initialize the directory
    MCP_DIR.mkdir(exist_ok=True)
    
    # Create the AltiumScript directory if it doesn't exist
    script_dir = MCP_DIR / "AltiumScript"
    script_dir.mkdir(exist_ok=True)
    
    # Verify configuration before starting
    if not altium_bridge.config.verify_paths():
        print("Warning: Configuration not complete. Some functionality may not work.")
    
    # Print status
    print(f"Altium executable: {altium_bridge.config.altium_exe_path}")
    print(f"Script path: {altium_bridge.config.script_path}")
    
    # Run the server
    mcp.run(transport='stdio')