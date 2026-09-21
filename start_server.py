import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

REQUIREMENTS = [
    "mcp[cli]==1.5.0",
    "pillow>=11.1.0",
    "pywin32>=310",
]


def _data_dir():
    # Per-user and OUTSIDE the extension folder: Claude Desktop replaces that
    # folder on every extension update, which used to throw the venv away and
    # put the slow first-launch build back inside the client's 60 s startup
    # timeout. ALTIUM_MCP_HOME overrides the location (tests, odd installs).
    override = os.environ.get("ALTIUM_MCP_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "altium-mcp"
    return SCRIPT_DIR / "server"


def _venv_key():
    # One venv per (requirements, interpreter version). Changing either gets a
    # fresh directory instead of mutating one another process may be using.
    text = "|".join(REQUIREMENTS) + "|py%d.%d" % sys.version_info[:2]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


VENVS_DIR = _data_dir() / "venvs"
VENV_DIR = VENVS_DIR / _venv_key()
PYTHON_EXE = VENV_DIR / "Scripts" / "python.exe"
MARKER = VENV_DIR / ".requirements-installed"
LOCK = VENVS_DIR / (VENV_DIR.name + ".lock")

# A lock this old belongs to a build that died. Take it over.
STALE_LOCK_SECONDS = 300
# How long to wait on another process's build before failing with instructions.
BUILD_WAIT_SECONDS = 90


def _marker_payload():
    return "\n".join(REQUIREMENTS)


def venv_is_ready():
    # python.exe alone is not enough: `python -m venv` can succeed and the pip
    # install still fail, leaving an interpreter with no mcp module. The marker
    # is written last and records what was installed, so a change to
    # REQUIREMENTS also invalidates it.
    if not PYTHON_EXE.exists():
        return False
    try:
        return MARKER.read_text(encoding="utf-8") == _marker_payload()
    except OSError:
        return False


def _manual_build_message(reason):
    pip_exe = VENV_DIR / "Scripts" / "pip.exe"
    requirements = " ".join('"%s"' % r for r in REQUIREMENTS)
    return (
        "%s\n\n"
        "Build the environment by hand, which is not subject to the client's\n"
        "startup timeout:\n"
        '  rmdir /s /q "%s"\n'
        '  "%s" -m venv "%s"\n'
        '  "%s" install %s'
        % (reason, VENV_DIR, sys.executable, VENV_DIR, pip_exe, requirements)
    )


def _acquire_lock():
    """True if this process owns the build, False if another process holds it."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - LOCK.stat().st_mtime
            except OSError:
                continue  # vanished between open and stat; try to claim it
            if age < STALE_LOCK_SECONDS:
                return False
            try:
                LOCK.unlink()
            except OSError:
                return False
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    return False


def _build_venv():
    # An existing tree here is a partial build or one whose requirements moved.
    # Either way it cannot be trusted. Removing it first is what stops
    # `python -m venv` failing with WinError 183 on a half-created directory.
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        if VENV_DIR.exists():
            raise RuntimeError(
                _manual_build_message(
                    "Could not remove the existing virtual environment at %s. "
                    "Something is holding it open." % VENV_DIR
                )
            )
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])
    subprocess.check_call(
        [str(VENV_DIR / "Scripts" / "pip.exe"), "install", "--quiet"] + REQUIREMENTS
    )
    MARKER.write_text(_marker_payload(), encoding="utf-8")
    _prune_old_venvs()


def _prune_old_venvs():
    # Venvs keyed to an older requirement list or interpreter. Best effort: one
    # still in use by a running server has locked files and simply stays.
    for child in VENVS_DIR.iterdir():
        if child.is_dir() and child != VENV_DIR:
            shutil.rmtree(child, ignore_errors=True)


def ensure_venv():
    if venv_is_ready():
        return str(PYTHON_EXE)

    if _acquire_lock():
        try:
            _build_venv()
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                _manual_build_message("Building the environment failed: %s" % exc)
            ) from exc
        finally:
            try:
                LOCK.unlink()
            except OSError:
                pass
        return str(PYTHON_EXE)

    # Someone else is genuinely mid-build. Wait for them rather than fighting
    # over the same directory, but do not wait forever.
    deadline = time.time() + BUILD_WAIT_SECONDS
    while time.time() < deadline:
        time.sleep(1)
        if venv_is_ready():
            return str(PYTHON_EXE)
        if not LOCK.exists():
            break  # builder exited without finishing

    raise RuntimeError(
        _manual_build_message(
            "The virtual environment at %s is not ready: another process was "
            "building it and did not finish." % VENV_DIR
        )
    )


if __name__ == "__main__":
    venv_python = ensure_venv()
    server_path = str(SCRIPT_DIR / "server" / "main.py")
    # main.py keeps config.json in the same per-user folder; hand it the
    # resolved location so the two can never disagree.
    env = dict(os.environ, ALTIUM_MCP_HOME=str(_data_dir()))
    sys.exit(subprocess.call([venv_python, server_path], env=env))
