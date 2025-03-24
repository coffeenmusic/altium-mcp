from mcp.server.fastmcp import FastMCP, Context
import json
import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any

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

# Initialize FastMCP server
mcp = FastMCP("AltiumMCP", description="Altium integration through the Model Context Protocol")

class AltiumBridge:
    def __init__(self):
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)
    
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
            logger.info(f"Waiting for user to run altium_bridge.pas in Altium...")
            
            # Wait for the response file
            timeout = 120  # seconds - longer timeout to allow for manual execution
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
    print(f">>> Please run the altium_bridge.pas script in Altium now <<<")
    
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
    print(f">>> Please run the altium_bridge.pas script in Altium now <<<")
    
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

@mcp.tool()
async def get_server_status(ctx: Context) -> str:
    """Get the current status of the Altium MCP server"""
    return "Server is running. Waiting for commands."

if __name__ == "__main__":
    logger.info("Starting Altium MCP Server...")
    logger.info(f"Using MCP directory: {MCP_DIR}")
    # Initialize the directory
    MCP_DIR.mkdir(exist_ok=True)
    # Run the server
    mcp.run(transport='stdio')