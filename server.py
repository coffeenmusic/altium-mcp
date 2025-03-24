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
DEFAULT_SCRIPT_PATH = MCP_DIR / "AltiumScript" / "Altium_API.PrjScr"  # Updated path

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
async def get_cmp_description(ctx: Context, cmp_designator: str) -> str:
    """
    Get the description of a component in Altium
    
    Args:
        cmp_designator (str): The designator of the component (e.g., "R1", "C5", "U3")
    
    Returns:
        str: The component description
    """
    logger.info(f"Getting description for component: {cmp_designator}")
    
    # Execute the command in Altium
    response = await altium_bridge.execute_command(
        "get_cmp_description",
        {"cmp_designator": cmp_designator}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component description: {error_msg}")
        return f"Error: {error_msg}"
    
    # Return the description
    description = response.get("result", "No description available")
    logger.info(f"Got description for {cmp_designator}: {description}")
    return description

@mcp.tool()
async def get_all_designators(ctx: Context) -> str:
    """
    Get all component designators from the current Altium board
    
    Returns:
        str: JSON array of all component designators on the current board
    """
    logger.info("Getting all component designators from current board")
    
    # Execute the command in Altium
    response = await altium_bridge.execute_command(
        "get_all_designators",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        raw_response = response.get("raw_response", "")
        logger.error(f"Error getting component designators: {error_msg}")
        if raw_response:
            logger.error(f"Raw response: {raw_response}")
        return f"Error: {error_msg}"
    
    # Get the designators list
    designators = response.get("result", [])
    logger.info(f"Result type: {type(designators)}")
    
    if isinstance(designators, str):
        logger.info(f"Result is a string, length: {len(designators)}")
        logger.info(f"First 100 chars: {designators[:100]}")
        
        try:
            # If the result is a string, try to parse it as JSON
            designators_list = json.loads(designators)
            logger.info(f"Successfully parsed designators string into JSON. Found {len(designators_list)} designators")
            return json.dumps(designators_list)
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing designators JSON: {e}")
            logger.error(f"Error at position {e.pos}, line {e.lineno}, column {e.colno}")
            logger.error(f"Character at error position: '{designators[e.pos:e.pos+10]}...'")
            
            # Try to manually extract the array if it looks like an array
            if designators.startswith('[') and designators.endswith(']'):
                logger.info("Designators string looks like a JSON array, returning as is")
                return designators
            return "[]"
    elif isinstance(designators, list):
        logger.info(f"Result is already a list with {len(designators)} items")
        return json.dumps(designators)
    else:
        logger.info(f"Result is of unexpected type: {type(designators)}")
        return "[]"

@mcp.tool()
async def get_server_status(ctx: Context) -> str:
    """Get the current status of the Altium MCP server"""
    status = {
        "server": "Running",
        "altium_exe": altium_bridge.config.altium_exe_path,
        "script_path": altium_bridge.config.script_path,
        "altium_found": os.path.exists(altium_bridge.config.altium_exe_path),
        "script_found": os.path.exists(altium_bridge.config.script_path)
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