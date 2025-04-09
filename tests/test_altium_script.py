"""
Altium Script Integration Tests

This script tests the integration between the MCP server and the Altium_API.pas script.
It focuses on validating that the Altium script correctly processes requests and generates
proper responses for various commands.
"""

import os
import sys
import json
import time
import unittest
import subprocess
from pathlib import Path

# Try to import psutil for better process management
try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False
    print("Warning: psutil not found. Install with 'pip install psutil' for better process management.")

# Add the parent directory to the path so we can import modules from the main project
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Constants
MCP_DIR = Path("C:/AltiumMCP")
REQUEST_FILE = MCP_DIR / "request.json"
RESPONSE_FILE = MCP_DIR / "response.json"
TIMEOUT = 120  # seconds

class AltiumScriptTest(unittest.TestCase):
    """Test cases for validating the Altium script functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Initialize the test environment."""
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)
        
        # Check if Altium is running
        print("IMPORTANT: Ensure Altium is running and a project is open.")
        print("           The test will wait for you to prepare Altium.")
        print("           When ready, press Enter to continue...")
        input()
    
    def setUp(self):
        """Set up each test."""
        # Remove any existing response file
        if RESPONSE_FILE.exists():
            RESPONSE_FILE.unlink()
    
    def tearDown(self):
        """Clean up after each test."""
        # Check if we have a process that needs to be terminated
        if hasattr(self, 'current_process') and self.current_process is not None:
            # Check if the process is still running
            if self.current_process.poll() is None:
                try:
                    print(f"Terminating process {self.current_process.pid}")
                    self.current_process.terminate()
                    # Wait a bit for the process to terminate
                    self.current_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    print(f"Process {self.current_process.pid} did not terminate in time, killing it")
                    self.current_process.kill()
            self.current_process = None
    
    @classmethod
    def tearDownClass(cls):
        """Clean up the test environment after all tests have completed."""
        print("Test suite completed. Cleaning up resources...")
        
        # Make sure all subprocess are terminated
        if HAVE_PSUTIL:
            try:
                current_process = psutil.Process()
                children = current_process.children(recursive=True)
                
                for child in children:
                    try:
                        print(f"Terminating child process: {child.pid}")
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
            except Exception as e:
                print(f"Error cleaning up processes: {e}")
        else:
            print("psutil not available. Some processes might still be running.")
            
            # Try to clean up using subprocess directly
            if hasattr(cls, 'current_process') and cls.current_process is not None:
                try:
                    if cls.current_process.poll() is None:
                        print(f"Terminating process {cls.current_process.pid}")
                        cls.current_process.terminate()
                except Exception as e:
                    print(f"Error terminating process: {e}")
    
    def execute_command(self, command, params=None):
        """
        Execute a command through the Altium script.
        
        Args:
            command (str): The command to execute
            params (dict): Parameters for the command
            
        Returns:
            dict: The response data
        """
        if params is None:
            params = {}
        
        # Clean up any existing response file
        if RESPONSE_FILE.exists():
            RESPONSE_FILE.unlink()
        
        # Create the request data
        request_data = {
            "command": command,
            **params
        }
        
        # Write the request to the file
        with open(REQUEST_FILE, 'w') as f:
            json.dump(request_data, f, indent=2)
        
        print(f"Sent command: {command}")
        print(f"Request data: {json.dumps(request_data, indent=2)}")
        
        # Run the Altium script using the same approach as in server.py
        print("Running Altium script...")
        
        # Get the Altium executable and script paths
        # You might need to adjust these paths based on your configuration
        altium_exe_path = self.get_altium_exe_path()
        script_path = self.get_script_path()
        
        # Check if paths are valid
        if not os.path.exists(altium_exe_path):
            self.fail(f"Altium executable not found at: {altium_exe_path}")
        
        if not os.path.exists(script_path):
            self.fail(f"Script file not found at: {script_path}")
        
        # Format the command to run the script in Altium
        run_command = f'"{altium_exe_path}" -RScriptingSystem:RunScript(ProjectName="{script_path}"^|ProcName="Altium_API>Run")'
        print(f"Executing: {run_command}")
        
        try:
            # Start the process
            import subprocess
            process = subprocess.Popen(run_command, shell=True)
            print(f"Launched Altium with script, process ID: {process.pid}")
            
            # Store the process so we can check its status later
            self.current_process = process
        except Exception as e:
            self.fail(f"Error launching Altium: {e}")
        
        # Wait for the response file with timeout
        print("Waiting for response file to appear...")
        start_time = time.time()
        while not RESPONSE_FILE.exists() and time.time() - start_time < TIMEOUT:
            time.sleep(0.5)
        
        if not RESPONSE_FILE.exists():
            self.fail(f"No response received within {TIMEOUT} seconds")
        
        # Read the response
        with open(RESPONSE_FILE, 'r') as f:
            response_text = f.read()
        
        print(f"Response received: {response_text[:200]}...")
        
        try:
            response = json.loads(response_text)
            
            # Print full response for examination
            print("\nFull response:")
            print(json.dumps(response, indent=2))
            
            # Wait for user input before continuing
            print("\nExamine the response and press Enter to continue...")
            input()
            
            return response
        except json.JSONDecodeError as e:
            self.fail(f"Failed to parse JSON response: {e}\nResponse: {response_text}")
    
    def get_altium_exe_path(self):
        """Get the path to the Altium executable."""
        # Try to read from config file
        config_file = MCP_DIR / "config.json"
        
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
                    return config.get("altium_exe_path", "")
            except Exception:
                pass
        
        # Default paths to check
        default_paths = [
            "C:/Program Files/Altium/AD22/X2.EXE",
            "C:/Program Files/Altium/AD21/X2.EXE",
            "C:/Program Files/Altium/AD20/X2.EXE",
            "C:/Program Files/Altium/AD19/X2.EXE",
        ]
        
        for path in default_paths:
            if os.path.exists(path):
                return path
        
        # If no path found, prompt user
        print("Altium executable not found. Please enter the path to X2.EXE:")
        return input().strip()
    
    def get_script_path(self):
        """Get the path to the Altium script."""
        # Try to read from config file
        config_file = MCP_DIR / "config.json"
        
        if config_file.exists():
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
                    return config.get("script_path", "")
            except Exception:
                pass
        
        # Default path
        default_path = "C:/AltiumMCP/AltiumScript/Altium_API.PrjScr"
        
        if os.path.exists(default_path):
            return default_path
        
        # If path not found, prompt user
        print("Altium script not found. Please enter the path to Altium_API.PrjScr:")
        return input().strip()
    
    def validate_component_data_response(self, response):
        """
        Validate the response from get_all_component_data command.
        
        Args:
            response (dict): The response to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        log_file = Path("tests/validation_failures.log")
        
        try:
            # Check if the response is successful
            if not response.get("success", False):
                self._log_failure(log_file, "Response indicates failure", response)
                return False
            
            # Check if result is a list
            result = response.get("result", [])
            if not isinstance(result, list):
                self._log_failure(log_file, "Result is not a list", response)
                return False
            
            # If list is empty, that's technically valid but suspicious
            if len(result) == 0:
                self._log_failure(log_file, "Result list is empty - no components found", response)
                return False
            
            # Check the structure of the first component
            component = result[0]
            expected_fields = [
                "designator", "name", "description", "footprint", 
                "layer", "x", "y", "width", "height", "rotation"
            ]
            
            missing_fields = []
            for field in expected_fields:
                if field not in component:
                    missing_fields.append(field)
            
            if missing_fields:
                self._log_failure(log_file, f"Component missing required fields: {', '.join(missing_fields)}", response)
                return False
            
            # Check data type for numeric fields
            numeric_fields = ["x", "y", "width", "height", "rotation"]
            invalid_numeric = []
            for field in numeric_fields:
                value = component.get(field)
                # Check if it's a number (int or float)
                if not isinstance(value, (int, float)):
                    invalid_numeric.append(field)
            
            if invalid_numeric:
                self._log_failure(log_file, f"Component has non-numeric values for fields: {', '.join(invalid_numeric)}", response)
                return False
            
            # If we got here, the response is valid
            print("SUCCESS: get_all_component_data response validation passed")
            return True
            
        except Exception as e:
            self._log_failure(log_file, f"Exception during validation: {str(e)}", response)
            return False
    
    def _log_failure(self, log_file, message, response=None):
        """Log a validation failure to a file."""
        # Create directory if it doesn't exist
        log_file.parent.mkdir(exist_ok=True)
        
        try:
            with open(log_file, "a") as f:
                f.write("\n" + "-"*50 + "\n")
                f.write(f"VALIDATION FAILURE: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Message: {message}\n")
                if response:
                    f.write("Response excerpt:\n")
                    try:
                        # Log just the first part of the response to keep the log file manageable
                        response_str = json.dumps(response, indent=2)[:500]
                        f.write(f"{response_str}...\n")
                    except:
                        f.write(f"{str(response)[:500]}...\n")
                f.write("-"*50 + "\n")
            
            print(f"FAILURE: {message}")
            print(f"Details logged to {log_file}")
        except Exception as e:
            print(f"Error writing to log file: {e}")
            print(f"FAILURE: {message}")
    
    def test_get_all_component_data(self):
        """Test the get_all_component_data command."""
        print("\n--- RUNNING TEST: get_all_component_data ---\n")
        
        # Execute the command
        response = self.execute_command("get_all_component_data")
        
        # Validate the response using our custom validation function
        is_valid = self.validate_component_data_response(response)
        
        # Continue with standard assertions if you want
        self.assertTrue(response.get("success", False), 
                      f"Command failed: {response.get('error', 'Unknown error')}")
        
        # Validate the result structure
        result = response.get("result", [])
        self.assertIsInstance(result, list, "Result should be a list")
        
        if len(result) > 0:
            # Check if at least one component has expected fields
            component = result[0]
            expected_fields = ["designator", "name", "description", "footprint", 
                              "layer", "x", "y", "width", "height", "rotation"]
            
            for field in expected_fields:
                self.assertIn(field, component, f"Component missing field: {field}")
    
    def test_get_component_pins(self):
        """Test the get_component_pins command."""
        print("\n--- RUNNING TEST: get_component_pins ---\n")
        
        # First, get all component designators
        all_components = self.execute_command("get_all_component_data")
        
        # Verify the response
        self.assertTrue(all_components.get("success", False), 
                       f"Failed to get component data: {all_components.get('error', 'Unknown error')}")
        
        result = all_components.get("result", [])
        if not result:
            self.skipTest("No components found in the board, skipping pin test")
        
        # Get designators for a few components (max 3)
        designators = [comp["designator"] for comp in result[:3] if "designator" in comp]
        
        if not designators:
            self.skipTest("No valid designators found in the board, skipping pin test")
        
        # Execute the get_component_pins command
        response = self.execute_command("get_component_pins", {"designators": designators})
        
        # Verify the response
        self.assertTrue(response.get("success", False), 
                       f"Command failed: {response.get('error', 'Unknown error')}")
        
        # Validate the result structure
        pins_data = response.get("result", [])
        self.assertIsInstance(pins_data, list, "Result should be a list")
        
        if pins_data:
            # Check if at least one component has expected fields
            component = pins_data[0]
            self.assertIn("designator", component, "Component missing 'designator' field")
            self.assertIn("pins", component, "Component missing 'pins' field")
            
            # If pins exist, check their structure
            if component.get("pins"):
                pin = component["pins"][0]
                expected_pin_fields = ["name", "net", "x", "y"]
                for field in expected_pin_fields:
                    self.assertIn(field, pin, f"Pin missing field: {field}")
    
    def test_get_selected_components_coordinates(self):
        """Test the get_selected_components_coordinates command."""
        print("\n--- RUNNING TEST: get_selected_components_coordinates ---\n")
        
        # This test requires user interaction to select components in Altium
        print("\nPLEASE SELECT AT LEAST ONE COMPONENT IN ALTIUM")
        print("Press Enter when components are selected...")
        input()
        
        # Execute the command
        response = self.execute_command("get_selected_components_coordinates")
        
        # Verify the response
        self.assertTrue(response.get("success", False), 
                       f"Command failed: {response.get('error', 'Unknown error')}")
        
        # Validate the result structure
        result = response.get("result", [])
        
        # The result should either be an array of components or a message indicating no selections
        if isinstance(result, list):
            if result:  # If there are selected components
                component = result[0]
                expected_fields = ["designator", "x", "y", "width", "height", "rotation"]
                for field in expected_fields:
                    self.assertIn(field, component, f"Selected component missing field: {field}")
            else:
                print("No components were selected in Altium")
        else:
            self.fail(f"Unexpected response format: {result}")
    
    def test_move_components(self):
        """Test the move_components command."""
        print("\n--- RUNNING TEST: move_components ---\n")
        
        # First, get all component designators
        all_components = self.execute_command("get_all_component_data")
        
        # Verify the response
        self.assertTrue(all_components.get("success", False), 
                       f"Failed to get component data: {all_components.get('error', 'Unknown error')}")
        
        result = all_components.get("result", [])
        if not result:
            self.skipTest("No components found in the board, skipping move test")
        
        # Get designators for a few components (max 3)
        designators = [comp["designator"] for comp in result[:3] if "designator" in comp]
        
        if not designators:
            self.skipTest("No valid designators found in the board, skipping move test")
        
        # Record original positions before move
        original_positions = {}
        for comp in result[:3]:
            if "designator" in comp and comp["designator"] in designators:
                original_positions[comp["designator"]] = {
                    "x": comp.get("x", 0),
                    "y": comp.get("y", 0)
                }
        
        # Execute the move_components command (move components by 100 mils in X and Y)
        move_params = {
            "cmp_designators": designators,
            "x_offset": 100,
            "y_offset": 100,
            "rotation": 0
        }
        
        response = self.execute_command("move_components", move_params)
        
        # Verify the response
        self.assertTrue(response.get("success", False), 
                       f"Command failed: {response.get('error', 'Unknown error')}")
        
        # Get components again to verify they moved
        all_components_after = self.execute_command("get_all_component_data")
        result_after = all_components_after.get("result", [])
        
        # Create a dict of components after move
        moved_positions = {}
        for comp in result_after:
            if "designator" in comp and comp["designator"] in designators:
                moved_positions[comp["designator"]] = {
                    "x": comp.get("x", 0),
                    "y": comp.get("y", 0)
                }
        
        # Verify components moved as expected
        for designator in designators:
            if designator in original_positions and designator in moved_positions:
                original = original_positions[designator]
                moved = moved_positions[designator]
                
                # Check that X and Y coordinates changed by approximately 100 mils
                # (Allow for small floating point differences)
                x_diff = abs((moved["x"] - original["x"]) - 100)
                y_diff = abs((moved["y"] - original["y"]) - 100)
                
                self.assertLess(x_diff, 1, f"Component {designator} did not move as expected in X direction")
                self.assertLess(y_diff, 1, f"Component {designator} did not move as expected in Y direction")
        
        # Move components back to original positions
        move_back_params = {
            "cmp_designators": designators,
            "x_offset": -100,
            "y_offset": -100,
            "rotation": 0
        }
        
        self.execute_command("move_components", move_back_params)


if __name__ == "__main__":
    unittest.main()