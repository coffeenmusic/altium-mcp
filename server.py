from mcp.server.fastmcp import FastMCP, Context
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
from PIL import Image
import io
import base64
import glob
import re

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG for more detailed logs
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Output to console
        logging.FileHandler('altium_mcp.log')  # Also log to file
    ]
)
logger = logging.getLogger("AltiumMCPServer")

# File paths
MCP_DIR = Path("C:/AltiumMCP")
REQUEST_FILE = MCP_DIR / "request.json"
RESPONSE_FILE = MCP_DIR / "response.json"
CONFIG_FILE = MCP_DIR / "config.json"
DEFAULT_SCRIPT_PATH = MCP_DIR / "AltiumScript" / "Altium_API.PrjScr"

# Initialize FastMCP server
mcp = FastMCP("AltiumMCP", description="Altium integration through the Model Context Protocol")

class AltiumConfig:
    def __init__(self):
        self.altium_exe_path = ""
        self.script_path = str(DEFAULT_SCRIPT_PATH)
        self.load_config()
    
    def load_config(self):
        """Load configuration from file or create default if it doesn't exist"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    config = json.load(f)
                    self.altium_exe_path = config.get("altium_exe_path", "")
                    self.script_path = config.get("script_path", str(DEFAULT_SCRIPT_PATH))
                logger.info(f"Loaded configuration from {CONFIG_FILE}")
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
    
    def save_config(self):
        """Save configuration to file"""
        config = {
            "altium_exe_path": self.altium_exe_path,
            "script_path": self.script_path
        }
        
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
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

class AltiumBridge:
    def __init__(self):
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)
        
        # Load configuration
        self.config = AltiumConfig()
        self.config.verify_paths()
    
    async def execute_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a command in Altium via the bridge script"""
        try:
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
            timeout = 120  # seconds
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
            # Updated command to run the script in Altium with the proper format
            script_path = self.config.script_path
            
            # Extract project name and procedure name
            script_dir = os.path.dirname(script_path)
            script_file = os.path.basename(script_path)
            
            # Command format: "X2.EXE" -RScriptingSystem:RunScript(ProjectName="path\file.PrjScr"|ProcName="ModuleName>Run")
            command = f'"{self.config.altium_exe_path}" -RScriptingSystem:RunScript(ProjectName="{script_path}"^|ProcName="Altium_API>Run")'
            
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
            "Only place pins on the left and right side of the symbol. "
            "Place power rail pins at the upper right, ground pins in the bottom left, "
            "no connect pins in the bottom right, inputs on the left, outputs on the right, "
            "and try to group other pins together by similar functionality (for example, SPI, I2C, RGMII, etc.). "
            "Always separate groups by 100mil gaps unless there is extra spacing, then space out groups equal distance from each other. "
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
async def create_schematic_symbol(ctx: Context, symbol_name: str, description: str, pins: list) -> str:
    """
    Before executing, run get_symbol_placement_rules first.
    Create a new schematic symbol in the current library with the specified pins
    Instructions: pins should be grouped together via function and only placed on 
                  the left and right side in 100 mil increments
    
    Args:
        symbol_name (str): Name of the symbol to create
        description (str): Description of the schematic symbol
        pins (list): List of pin data in format ["pin_number|pin_name|pin_type|pin_orientation|x|y", ...]
                    Pin types: eElectricHiZ, eElectricInput, eElectricIO, eElectricOpenCollector,
                               eElectricOpenEmitter, eElectricOutput, eElectricPassive, eElectricPower
                    Pin orientations: eRotate0 (right), eRotate90 (down), eRotate180 (left), eRotate270 (up)
                    X,Y coordinates in mils
    
    Returns:
        str: JSON object with the result of the component creation
    """
    logger.info(f"Creating schematic symbol: {symbol_name} with {len(pins)} pins")
    
    # Execute the command in Altium to create a symbol with pins
    response = await altium_bridge.execute_command(
        "create_schematic_symbol",
        {
            "symbol_name": symbol_name,
            "description": description,
            "pins": pins
        }
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
        str: JSON object with pin data for requested designators
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
async def move_components(ctx: Context, cmp_designators: list, x_offset: float, y_offset: float, rotation: float = 0) -> str:
    """
    Move selected components by specified X and Y offsets in the PCB layout
    
    Args:
        cmp_designators (list): List of designators of the components to move (e.g., ["R1", "C5", "U3"])
        x_offset (float): X offset distance in mils
        y_offset (float): Y offset distance in mils
        rotation (float): New rotation angle in degrees (0-360), if 0 the rotation is not changed
    
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
async def get_screenshot(ctx: Context, view_type: str = "pcb") -> str:
    """
    Take a screenshot of the Altium window
    
    Args:
        view_type (str): Type of view to capture - 'pcb' or 'sch'
    
    Returns:
        str: JSON object with screenshot data (base64 encoded) and metadata
    """
    logger.info(f"Taking screenshot of Altium {view_type} window")
    
    try:
        # First, execute the Altium command to ensure the right document type is focused
        response = await altium_bridge.execute_command(
            "take_view_screenshot", 
            {"view_type": view_type.lower()}
        )
        
        # Check for success
        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            logger.error(f"Error focusing {view_type} document: {error_msg}")
            return json.dumps({"success": False, "error": f"Failed to focus the correct document type: {error_msg}"})
        
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
                
                # Use the first matching window
                window = altium_windows[0]
                hwnd = window["handle"]
                
                # Get window dimensions
                left, top, right, bottom = window["rect"]
                width = right - left
                height = bottom - top
                
                if width <= 0 or height <= 0:
                    result_queue.put({"success": False, "error": f"Invalid window dimensions: {width}x{height}"})
                    return
                
                # Try to activate the window
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    time.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Could not bring window to foreground: {e}")
                
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
                    
                    # Save a local copy of the screenshot for debugging
                    debug_filename = f"C:/AltiumMCP/screenshot_{view_type}.png"
                    img.save(debug_filename)
                    logger.info(f"Saved debug screenshot to {debug_filename}")
                    
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
        return json.dumps(result)
    
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