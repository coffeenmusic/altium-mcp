import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
VENV_DIR = SCRIPT_DIR / "server" / ".venv"
PYTHON_EXE = VENV_DIR / "Scripts" / "python.exe"
MARKER = VENV_DIR / ".requirements-installed"
LOCK = VENV_DIR.parent / ".venv.lock"

REQUIREMENTS = [
    "mcp[cli]==1.5.0",
    "pillow>=11.1.0",
    "pywin32>=310",
]

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
    sys.exit(subprocess.call([venv_python, server_path]))
