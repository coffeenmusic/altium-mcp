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
import argparse

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
VERBOSITY_NORMAL = 0   # Show only major test events and results
VERBOSITY_DETAILED = 1  # Show commands and brief responses
VERBOSITY_DEBUG = 2    # Show everything

verbosity_level = VERBOSITY_NORMAL

def vprint(message, level=VERBOSITY_NORMAL):
    """Print message only if verbosity level is high enough."""
    if verbosity_level >= level:
        print(message)

class AltiumScriptTest(unittest.TestCase):
    """Test cases for validating the Altium script functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Initialize the test environment."""
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)
        
        # Check if Altium is running
        vprint("IMPORTANT: Ensure Altium is running and a project is open.", VERBOSITY_NORMAL)
        vprint("           The test will wait for you to prepare Altium.", VERBOSITY_NORMAL)
        vprint("           When ready, press Enter to continue...", VERBOSITY_NORMAL)
        input()
    
    def setUp(self):
        """Set up each test."""
        # Remove any existing response file
        if RESPONSE_FILE.exists():
            RESPONSE_FILE.unlink()
    
    def tearDown(self):
        """Clean up after each test."""
        import signal
        import os
        
        # Check if we have a process that needs to be terminated
        if hasattr(self, 'current_process') and self.current_process is not None:
            # Check if the process is still running
            try:
                if self.current_process.poll() is None:
                    vprint(f"Terminating process {self.current_process.pid}", VERBOSITY_DETAILED)
                    
                    # First try graceful termination
                    try:
                        self.current_process.terminate()
                        # Wait for termination
                        try:
                            self.current_process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            vprint(f"Process {self.current_process.pid} did not terminate gracefully, killing it", VERBOSITY_DETAILED)
                            
                            # If terminate didn't work, use SIGKILL (force kill)
                            try:
                                if os.name == 'nt':  # Windows
                                    self.current_process.kill()
                                else:  # Unix/Linux/Mac
                                    os.kill(self.current_process.pid, signal.SIGKILL)
                                
                                # Wait a short time to verify the process is dead
                                try:
                                    self.current_process.wait(timeout=2)
                                except subprocess.TimeoutExpired:
                                    vprint(f"WARNING: Process {self.current_process.pid} could not be killed!", VERBOSITY_NORMAL)
                            except Exception as e:
                                vprint(f"Error during force kill: {e}", VERBOSITY_DETAILED)
                    except Exception as e:
                        vprint(f"Error during termination: {e}", VERBOSITY_DETAILED)
            except Exception as e:
                vprint(f"Error checking process status: {e}", VERBOSITY_DETAILED)
            
            # Explicitly set to None to clear the reference
            self.current_process = None
    
    @classmethod
    def tearDownClass(cls):
        """Clean up the test environment after all tests have completed."""
        vprint("Test suite completed. Cleaning up resources...", VERBOSITY_NORMAL)
        
        # Make sure all subprocess are terminated
        if HAVE_PSUTIL:
            try:
                current_process = psutil.Process()
                children = current_process.children(recursive=True)
                
                for child in children:
                    try:
                        vprint(f"Terminating child process: {child.pid}", VERBOSITY_DETAILED)
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
            except Exception as e:
                vprint(f"Error cleaning up processes: {e}", VERBOSITY_NORMAL)
        else:
            vprint("psutil not available. Some processes might still be running.", VERBOSITY_NORMAL)
            
            # Try to clean up using subprocess directly
            if hasattr(cls, 'current_process') and cls.current_process is not None:
                try:
                    if cls.current_process.poll() is None:
                        vprint(f"Terminating process {cls.current_process.pid}", VERBOSITY_DETAILED)
                        cls.current_process.terminate()
                except Exception as e:
                    vprint(f"Error terminating process: {e}", VERBOSITY_NORMAL)
    
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
        
        vprint(f"Sent command: {command}", VERBOSITY_DETAILED)
        vprint(f"Request data: {json.dumps(request_data, indent=2)}", VERBOSITY_DEBUG)
        
        # Run the Altium script using the same approach as in server.py
        vprint("Running Altium script...", VERBOSITY_DETAILED)
        
        # Get the Altium executable and script paths
        altium_exe_path = self.get_altium_exe_path()
        script_path = self.get_script_path()
        
        # Check if paths are valid
        if not os.path.exists(altium_exe_path):
            self.fail(f"Altium executable not found at: {altium_exe_path}")
        
        if not os.path.exists(script_path):
            self.fail(f"Script file not found at: {script_path}")
        
        # Format the command to run the script in Altium
        run_command = f'"{altium_exe_path}" -RScriptingSystem:RunScript(ProjectName="{script_path}"^|ProcName="Altium_API>Run")'
        vprint(f"Executing: {run_command}", VERBOSITY_DEBUG)
        
        try:
            # Start the process
            import subprocess
            process = subprocess.Popen(run_command, shell=True)
            vprint(f"Launched Altium with script, process ID: {process.pid}", VERBOSITY_DETAILED)
            
            # Store the process so we can check its status later
            if hasattr(self, 'current_process') and self.current_process is not None:
                # Terminate any existing process before starting a new one
                self._terminate_process(self.current_process)
                
            self.current_process = process
        except Exception as e:
            self.fail(f"Error launching Altium: {e}")
        
        # Wait for the response file with timeout
        vprint("Waiting for response file to appear...", VERBOSITY_DETAILED)
        start_time = time.time()
        while not RESPONSE_FILE.exists() and time.time() - start_time < TIMEOUT:
            time.sleep(0.5)
        
        if not RESPONSE_FILE.exists():
            self.fail(f"No response received within {TIMEOUT} seconds")
        
        # Read the response
        with open(RESPONSE_FILE, 'r') as f:
            response_text = f.read()
        
        vprint(f"Response received: {response_text[:200]}...", VERBOSITY_DETAILED)
        
        try:
            response = json.loads(response_text)
            
            # Print full response for examination
            vprint("\nFull response:", VERBOSITY_DEBUG)
            vprint(json.dumps(response, indent=2), VERBOSITY_DEBUG)
            
            # Wait for user input before continuing
            if verbosity_level >= VERBOSITY_DEBUG:
                vprint("\nExamine the response and press Enter to continue...", VERBOSITY_DEBUG)
                input()
            
            # After getting the response, terminate the process
            self._terminate_process(self.current_process)
            self.current_process = None
            
            return response
        except json.JSONDecodeError as e:
            self.fail(f"Failed to parse JSON response: {e}\nResponse: {response_text}")

    def _terminate_process(self, process):
        """Helper method to terminate a process."""
        import signal
        import os
        
        if process is None:
            return
        
        try:
            if process.poll() is None:
                # First try graceful termination
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # If terminate didn't work, use force kill
                    if os.name == 'nt':  # Windows
                        process.kill()
                    else:  # Unix/Linux/Mac
                        os.kill(process.pid, signal.SIGKILL)
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        vprint(f"WARNING: Process {process.pid} could not be killed!", VERBOSITY_NORMAL)
        except Exception as e:
            vprint(f"Error terminating process: {e}", VERBOSITY_DETAILED)
    
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
        vprint("\n--- RUNNING TEST: get_all_component_data ---\n", VERBOSITY_DEBUG)
        
        # Execute the command
        response = self.execute_command("get_all_component_data")
        
        # Validate using our comprehensive function and convert to unittest assertion
        self.assertTrue(self.validate_component_data_response(response), "Component data validation failed. Check validation_failures.log for details.")
    
        vprint("\nSUCCESS: test_get_component_pins completed\n", VERBOSITY_NORMAL)

    def test_get_component_pins(self):
        """Test the get_component_pins command."""
        vprint("\n--- RUNNING TEST: get_component_pins ---\n", VERBOSITY_DEBUG)
        
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

        print("\nSUCCESS: test_get_component_pins completed\n")
    
    def test_get_selected_components_coordinates(self):
        """Test the get_selected_components_coordinates command."""
        vprint("\n--- RUNNING TEST: get_selected_components_coordinates ---\n", VERBOSITY_NORMAL)
        
        # This test requires user interaction to select components in Altium
        vprint("\nPLEASE SELECT AT LEAST ONE COMPONENT IN ALTIUM", VERBOSITY_NORMAL)
        vprint("Press Enter when components are selected...", VERBOSITY_NORMAL)
        input()
        
        # Execute the command
        response = self.execute_command("get_selected_components_coordinates")
        
        # Store the response for later use in test_move_components
        # We'll use a class variable to share this data between test methods
        type(self).selected_components_response = response
        
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
                vprint(f"Found {len(result)} selected components", VERBOSITY_NORMAL)
            else:
                vprint("No components were selected in Altium", VERBOSITY_NORMAL)
                self.skipTest("No components were selected, skipping test")
        else:
            self.fail(f"Unexpected response format: {result}")
        
        vprint("\nSUCCESS: test_get_selected_components_coordinates completed\n", VERBOSITY_NORMAL)
        
        return response  # Return for convenience

    def test_move_components(self):
        """Test the move_components command."""
        vprint("\n--- RUNNING TEST: move_components ---\n", VERBOSITY_NORMAL)
        
        # Check if we have selected components from the previous test
        if not hasattr(type(self), 'selected_components_response') or not type(self).selected_components_response:
            # If not, run the selection test to get components
            vprint("No selected components found. Running selection test first...", VERBOSITY_NORMAL)
            response = self.test_get_selected_components_coordinates()
        else:
            response = type(self).selected_components_response
        
        # Verify we have selected components to work with
        result = response.get("result", [])
        if not result or not isinstance(result, list) or len(result) == 0:
            self.skipTest("No components selected, skipping move test")
        
        # Get designators from the selected components
        designators = [comp["designator"] for comp in result if "designator" in comp]
        
        if not designators:
            self.skipTest("No valid designators found in selected components, skipping move test")
        
        # Record original positions before move
        original_positions = {}
        for comp in result:
            if "designator" in comp and comp["designator"] in designators:
                original_positions[comp["designator"]] = {
                    "x": comp.get("x", 0),
                    "y": comp.get("y", 0)
                }
        
        # Execute the move_components command (move components by 100 mils in X and Y)
        # IMPORTANT: changed from cmp_designators to designators to match Altium script expectations
        move_params = {
            "designators": designators,  # This is the key change - parameter name must match what the script expects
            "x_offset": 100,
            "y_offset": 100,
            "rotation": 0
        }
        
        move_response = self.execute_command("move_components", move_params)
        
        # Verify the response
        self.assertTrue(move_response.get("success", False), 
                    f"Command failed: {move_response.get('error', 'Unknown error')}")
        
        # Get the new positions of the components
        vprint("\nPLEASE VERIFY COMPONENTS MOVED IN ALTIUM", VERBOSITY_NORMAL)
        vprint("Press Enter to continue...", VERBOSITY_NORMAL)
        input()
        
        # Get components again to verify they moved
        components_after = self.execute_command("get_selected_components_coordinates")
        result_after = components_after.get("result", [])
        
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
                vprint(f"Component {designator} moved successfully", VERBOSITY_DETAILED)
        
        # Move components back to original positions
        move_back_params = {
            "designators": designators,  # Also changed here
            "x_offset": -100,
            "y_offset": -100,
            "rotation": 0
        }
        
        vprint("Moving components back to original positions...", VERBOSITY_NORMAL)
        self.execute_command("move_components", move_back_params)
        
        vprint("\nSUCCESS: test_move_components completed\n", VERBOSITY_NORMAL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Altium Script Integration Tests')
    parser.add_argument('-v', '--verbose', action='count', default=0, 
                        help='Increase verbosity level (use -v for detailed output, -vv for debug output)')
    
    args = parser.parse_args()
    verbosity_level = args.verbose
    
    unittest.main(argv=['first-arg-is-ignored'])  # Override argv to ignore our args