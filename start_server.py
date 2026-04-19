import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
VENV_DIR = SCRIPT_DIR / "server" / ".venv"
REQUIREMENTS = [
    "mcp[cli]==1.5.0",
    "pillow>=11.1.0",
    "pywin32>=310",
]

def ensure_venv():
    python_exe = VENV_DIR / "Scripts" / "python.exe"
    if python_exe.exists():
        return str(python_exe)

    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    pip_exe = str(VENV_DIR / "Scripts" / "pip.exe")
    subprocess.check_call([pip_exe, "install", "--quiet"] + REQUIREMENTS)
    return str(python_exe)

if __name__ == "__main__":
    venv_python = ensure_venv()
    server_path = str(SCRIPT_DIR / "server" / "main.py")
    sys.exit(subprocess.call([venv_python, server_path]))
