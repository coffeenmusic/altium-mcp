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
COMPONENT_DATA_FILE = MCP_DIR / "component_data.json"
SCHEMATIC_DATA_FILE = MCP_DIR / "schematic_data.json"

# Initialize FastMCP server
mcp = FastMCP("AltiumMCP", description="Altium integration through the Model Context Protocol")

class ComponentDataManager:
    """Class to manage component data"""
    def __init__(self):
        self.component_data = {}
        self.is_loaded = False
        self.is_initialized = False
    
    def load_data(self):
        """Load component data from file if it exists"""
        if COMPONENT_DATA_FILE.exists():
            try:
                with open(COMPONENT_DATA_FILE, "r") as f:
                    self.component_data = json.load(f)
                logger.info(f"Loaded component data from {COMPONENT_DATA_FILE}")
                self.is_loaded = True
                return True
            except Exception as e:
                logger.error(f"Error loading component data: {e}")
                return False
        else:
            logger.info("No component data file found")
            return False
    
    def save_data(self, data):
        """Save component data to file"""
        try:
            # Create a dictionary to quickly access components by designator
            indexed_data = {}
            
            try:
                # Try parsing the data as a string first (in case it's a JSON string)
                if isinstance(data, str):
                    components_list = json.loads(data)
                else:
                    components_list = data
                
                # Create an index by designator
                for component in components_list:
                    if isinstance(component, dict) and "designator" in component:
                        indexed_data[component["designator"]] = component
            except Exception as e:
                logger.error(f"Error parsing component data: {e}")
                return False
            
            # Save the indexed data to file
            with open(COMPONENT_DATA_FILE, "w") as f:
                json.dump(indexed_data, f, indent=2)
            
            self.component_data = indexed_data
            self.is_loaded = True
            self.is_initialized = True
            logger.info(f"Saved component data to {COMPONENT_DATA_FILE}")
            return True
        except Exception as e:
            logger.error(f"Error saving component data: {e}")
            return False
    
    async def ensure_initialized(self, bridge):
        """Ensure component data is initialized (lazy initialization)"""
        # If already initialized, return immediately
        if self.is_initialized:
            return True
        
        # Always fetch fresh data from Altium on first request
        logger.info("Initializing component data on first request...")
        
        # Execute the command in Altium to get all component data
        response = await bridge.execute_command(
            "get_all_component_data",
            {}  # No parameters needed
        )
        
        # Check for success
        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            logger.error(f"Error getting component data: {error_msg}")
            return False
        
        # Get the component data
        component_data = response.get("result", [])
        
        # Save the component data
        success = self.save_data(component_data)
        
        if success:
            logger.info("Component data initialized successfully")
            self.is_initialized = True
            return True
        else:
            logger.error("Failed to initialize component data")
            return False
    
    def get_component(self, designator):
        """Get component data by designator"""
        if not self.is_loaded:
            if not self.load_data():
                return None
        
        return self.component_data.get(designator)
    
    def get_all_components(self):
        """Get all components data"""
        if not self.is_loaded:
            if not self.load_data():
                return []
        
        return list(self.component_data.values())
    
class SchematicDataManager:
    """Class to manage schematic component data"""
    def __init__(self):
        self.schematic_data = {}
        self.is_loaded = False
        self.is_initialized = False
    
    def load_data(self):
        """Load schematic data from file if it exists"""
        if SCHEMATIC_DATA_FILE.exists():
            try:
                with open(SCHEMATIC_DATA_FILE, "r") as f:
                    self.schematic_data = json.load(f)
                logger.info(f"Loaded schematic data from {SCHEMATIC_DATA_FILE}")
                self.is_loaded = True
                return True
            except Exception as e:
                logger.error(f"Error loading schematic data: {e}")
                return False
        else:
            logger.info("No schematic data file found")
            return False
    
    def save_data(self, data):
        """Save schematic data to file"""
        try:
            # Create a dictionary to quickly access components by designator
            indexed_data = {}
            
            try:
                # Try parsing the data as a string first (in case it's a JSON string)
                if isinstance(data, str):
                    components_list = json.loads(data)
                else:
                    components_list = data
                
                # Create an index by designator
                for component in components_list:
                    if isinstance(component, dict) and "designator" in component:
                        indexed_data[component["designator"]] = component
            except Exception as e:
                logger.error(f"Error parsing schematic data: {e}")
                return False
            
            # Save the indexed data to file
            with open(SCHEMATIC_DATA_FILE, "w") as f:
                json.dump(indexed_data, f, indent=2)
            
            self.schematic_data = indexed_data
            self.is_loaded = True
            self.is_initialized = True
            logger.info(f"Saved schematic data to {SCHEMATIC_DATA_FILE}")
            return True
        except Exception as e:
            logger.error(f"Error saving schematic data: {e}")
            return False
    
    async def ensure_initialized(self, bridge):
        """Ensure schematic data is initialized (lazy initialization)"""
        # If already initialized, return immediately
        if self.is_initialized:
            return True
        
        # Always fetch fresh data from Altium on first request
        logger.info("Initializing schematic data on first request...")
        
        # Execute the command in Altium to get all schematic data
        response = await bridge.execute_command(
            "get_schematic_data",
            {}  # No parameters needed
        )
        
        # Check for success
        if not response.get("success", False):
            error_msg = response.get("error", "Unknown error")
            logger.error(f"Error getting schematic data: {error_msg}")
            return False
        
        # Get the schematic data
        schematic_data = response.get("result", [])
        
        # Save the schematic data
        success = self.save_data(schematic_data)
        
        if success:
            logger.info("Schematic data initialized successfully")
            self.is_initialized = True
            return True
        else:
            logger.error("Failed to initialize schematic data")
            return False
    
    def get_component(self, designator):
        """Get schematic component data by designator"""
        if not self.is_loaded:
            if not self.load_data():
                return None
        
        return self.schematic_data.get(designator)
    
    def get_all_components(self):
        """Get all schematic components data"""
        if not self.is_loaded:
            if not self.load_data():
                return []
        
        return list(self.schematic_data.values())

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
        """Create a default configuration file"""
        # Try to find Altium executable in common locations
        altium_paths = [
            r"C:\Program Files\Altium\AD19\X2.EXE",
            r"C:\Program Files\Altium\AD20\X2.EXE",
            r"C:\Program Files\Altium\AD21\X2.EXE",
            r"C:\Program Files\Altium\AD22\X2.EXE",
            r"C:\Program Files\Altium\AD23\X2.EXE",
            r"C:\Program Files\Altium\AD24\X2.EXE",
        ]
        
        for path in altium_paths:
            if os.path.exists(path):
                self.altium_exe_path = path
                break
        
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
        # Only initialize tkinter root if needed
        root = None
        
        # Check Altium executable
        if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
            if root is None:
                root = tk.Tk()
                root.withdraw()  # Hide the main window
            
            print("Altium executable not found. Please select the Altium X2.EXE file...")
            self.altium_exe_path = filedialog.askopenfilename(
                title="Select Altium Executable",
                filetypes=[("Executable files", "*.exe")],  # Only allow .exe files
                initialdir="C:/Program Files/Altium"
            )
            
            if not self.altium_exe_path:
                logger.error("No Altium executable selected. Some functionality may not work.")
                print("Warning: No Altium executable selected. Automatic script execution will be disabled.")
        
        # Check script path
        if not os.path.exists(self.script_path):
            if root is None:
                root = tk.Tk()
                root.withdraw()  # Hide the main window
            
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
        
        # Clean up tkinter root if created
        if root is not None:
            root.destroy()
        
        # Save the updated configuration
        self.save_config()
        
        return os.path.exists(self.altium_exe_path) and os.path.exists(self.script_path)

class AltiumBridge:
    def __init__(self):
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)
        
        # Load configuration
        self.config = AltiumConfig()
        self.config.verify_paths()
        
        # Initialize data managers
        self.component_manager = ComponentDataManager()
        self.schematic_manager = SchematicDataManager()
    
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
    
    # Ensure data is initialized (lazy initialization on first request)
    initialized = await altium_bridge.component_manager.ensure_initialized(altium_bridge)
    
    if not initialized:
        logger.error("Component data could not be initialized")
        return json.dumps({"error": "Failed to initialize component data"})
    
    # Get all components from cache
    components = altium_bridge.component_manager.get_all_components()
    
    if not components:
        logger.info("No component data found in cache")
        return json.dumps({"error": "No component data found"})
    
    # Extract all unique property names from all components
    property_names = set()
    for component in components:
        property_names.update(component.keys())
    
    # Convert set to sorted list for consistent output
    property_list = sorted(list(property_names))
    
    logger.info(f"Found {len(property_list)} unique property names")
    return json.dumps(property_list, indent=2)

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
    
    # Ensure data is initialized (lazy initialization on first request)
    initialized = await altium_bridge.component_manager.ensure_initialized(altium_bridge)
    
    if not initialized:
        logger.error("Component data could not be initialized")
        return json.dumps({"error": "Failed to initialize component data"})
    
    # Get all components from cache
    components = altium_bridge.component_manager.get_all_components()
    
    if not components:
        logger.info("No component data found in cache")
        return json.dumps({"error": "No component data found"})
    
    # Extract the property values along with designators
    property_values = []
    for component in components:
        designator = component.get("designator")
        if designator and property_name in component:
            property_values.append({
                "designator": designator,
                "value": component.get(property_name)
            })
    
    logger.info(f"Found {len(property_values)} components with property '{property_name}'")
    return json.dumps(property_values, indent=2)

@mcp.tool()
async def get_schematic_data(ctx: Context, cmp_designator: str) -> str:
    """
    Get schematic data for a component in Altium
    
    Args:
        cmp_designator (str): The designator of the component (e.g., "R1", "C5", "U3")
    
    Returns:
        str: JSON object with schematic component data
    """
    logger.info(f"Getting schematic data for component: {cmp_designator}")
    
    # Ensure data is initialized (lazy initialization on first request)
    initialized = await altium_bridge.schematic_manager.ensure_initialized(altium_bridge)
    
    if not initialized:
        logger.error("Schematic data could not be initialized")
        return json.dumps({"error": "Failed to initialize schematic data"})
    
    # Get schematic data from cache
    component = altium_bridge.schematic_manager.get_component(cmp_designator)
    
    if component:
        logger.info(f"Found schematic data for {cmp_designator} in cache")
        return json.dumps(component, indent=2)
    else:
        logger.info(f"Schematic data for {cmp_designator} not found in cache")
        return json.dumps({"error": f"Schematic data for component {cmp_designator} not found"})

@mcp.tool()
async def get_component_data(ctx: Context, cmp_designator: str) -> str:
    """
    Get all data for a component in Altium
    
    Args:
        cmp_designator (str): The designator of the component (e.g., "R1", "C5", "U3")
    
    Returns:
        str: JSON object with all component data
    """
    logger.info(f"Getting data for component: {cmp_designator}")
    
    # Ensure data is initialized (lazy initialization on first request)
    initialized = await altium_bridge.component_manager.ensure_initialized(altium_bridge)
    
    if not initialized:
        logger.error("Component data could not be initialized")
        return json.dumps({"error": "Failed to initialize component data"})
    
    # Get component data from cache
    component = altium_bridge.component_manager.get_component(cmp_designator)
    
    if component:
        logger.info(f"Found component data for {cmp_designator} in cache")
        return json.dumps(component, indent=2)
    else:
        logger.info(f"Component {cmp_designator} not found in cache")
        return json.dumps({"error": f"Component {cmp_designator} not found"})
    
@mcp.tool()
async def get_combined_component_data(ctx: Context, cmp_designator: str) -> str:
    """
    Get combined PCB and schematic data for a component in Altium
    
    Args:
        cmp_designator (str): The designator of the component (e.g., "R1", "C5", "U3")
    
    Returns:
        str: JSON object with combined component data
    """
    logger.info(f"Getting combined data for component: {cmp_designator}")
    
    # Ensure PCB data is initialized
    pcb_initialized = await altium_bridge.component_manager.ensure_initialized(altium_bridge)
    
    if not pcb_initialized:
        logger.error("PCB data could not be initialized")
        return json.dumps({"error": "Failed to initialize PCB data"})
    
    # Get PCB data from cache
    pcb_component = altium_bridge.component_manager.get_component(cmp_designator)
    
    if not pcb_component:
        logger.info(f"PCB data for {cmp_designator} not found in cache")
        return json.dumps({"error": f"PCB data for component {cmp_designator} not found"})
    
    # Try to get schematic data if available
    try:
        # Ensure schematic data is initialized
        schem_initialized = await altium_bridge.schematic_manager.ensure_initialized(altium_bridge)
        
        if schem_initialized:
            # Get schematic data from cache
            schem_component = altium_bridge.schematic_manager.get_component(cmp_designator)
            
            if schem_component:
                # Combine the data
                combined_data = pcb_component.copy()
                
                # Add schematic data fields
                for key, value in schem_component.items():
                    if key != "designator" and key not in combined_data:
                        combined_data[key] = value
                
                logger.info(f"Created combined data for {cmp_designator}")
                return json.dumps(combined_data, indent=2)
    except Exception as e:
        logger.error(f"Error combining data: {e}")
    
    # If schematic data not available or error occurred, just return PCB data
    logger.info(f"Returning PCB-only data for {cmp_designator}")
    return json.dumps(pcb_component, indent=2)

@mcp.tool()
async def get_all_designators(ctx: Context) -> str:
    """
    Get all component designators from the current Altium board
    
    Returns:
        str: JSON array of all component designators on the current board
    """
    logger.info("Getting all component designators")
    
    # Ensure data is initialized (lazy initialization on first request)
    initialized = await altium_bridge.component_manager.ensure_initialized(altium_bridge)
    
    if not initialized:
        logger.error("Component data could not be initialized")
        return json.dumps({"error": "Failed to initialize component data"})
    
    # Get all components from cache
    components = altium_bridge.component_manager.get_all_components()
    
    # Extract designators
    designators = [comp.get("designator") for comp in components if "designator" in comp]
    
    logger.info(f"Found {len(designators)} designators")
    return json.dumps(designators)

@mcp.tool()
async def get_server_status(ctx: Context) -> str:
    """Get the current status of the Altium MCP server"""
    status = {
        "server": "Running",
        "altium_exe": altium_bridge.config.altium_exe_path,
        "script_path": altium_bridge.config.script_path,
        "altium_found": os.path.exists(altium_bridge.config.altium_exe_path),
        "script_found": os.path.exists(altium_bridge.config.script_path),
        "pcb_data": {
            "loaded": altium_bridge.component_manager.is_loaded,
            "initialized": altium_bridge.component_manager.is_initialized,
            "component_count": len(altium_bridge.component_manager.component_data) if altium_bridge.component_manager.is_loaded else 0
        },
        "schematic_data": {
            "loaded": altium_bridge.schematic_manager.is_loaded,
            "initialized": altium_bridge.schematic_manager.is_initialized,
            "component_count": len(altium_bridge.schematic_manager.schematic_data) if altium_bridge.schematic_manager.is_loaded else 0
        }
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