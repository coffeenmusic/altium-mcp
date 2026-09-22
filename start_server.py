"""Bootstrap for the Altium MCP extension.

Two roles in one file:

  python start_server.py           the LAUNCHER Claude Desktop runs. Makes
                                   sure a ready venv exists, then execs
                                   server/main.py inside it.
  python start_server.py --build   the BUILDER. Creates the venv and installs
                                   the pinned requirements, then exits.

The builder is a separate, DETACHED process on purpose. Claude Desktop gives
a server about 60 s to answer `initialize` and then kills it, and it also
restarts servers freely (extension install, app update, new session). A pip
install that runs inside the launcher dies with it, leaving a half-built venv
and a lock nobody will release. A detached builder finishes regardless, so a
restarted launcher finds the venv ready or a build genuinely in progress.
"""
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
    # Per-user and OUTSIDE both the extension folder and AppData. Claude
    # Desktop replaces the extension folder on every update, which used to
    # throw the venv away and put the slow first build back inside the
    # client's startup timeout. AppData is out because Claude Desktop is an
    # MSIX package: writes its child processes make under AppData are
    # redirected into the package's LocalCache, so a venv built from a normal
    # terminal and one built by the server would be in different places.
    # The profile root is not redirected. ALTIUM_MCP_HOME overrides this.
    override = os.environ.get("ALTIUM_MCP_HOME")
    if override:
        return Path(override)
    return Path.home() / ".altium-mcp"


def _venv_key():
    # One venv per (requirements, interpreter version). Changing either gets a
    # fresh directory instead of mutating one another process may be using.
    text = "|".join(REQUIREMENTS) + "|py%d.%d" % sys.version_info[:2]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


DATA_DIR = _data_dir()
VENVS_DIR = DATA_DIR / "venvs"
VENV_DIR = VENVS_DIR / _venv_key()
PYTHON_EXE = VENV_DIR / "Scripts" / "python.exe"
MARKER = VENV_DIR / ".requirements-installed"
LOCK = VENVS_DIR / (VENV_DIR.name + ".lock")
BUILD_LOG = DATA_DIR / "build.log"

# How long the launcher waits for the venv before giving up on THIS start.
# Just under Claude Desktop's ~60 s initialize timeout, so the launcher gets
# to say why it is exiting instead of being killed mid-sentence.
LAUNCH_WAIT_SECONDS = 50
# A lock whose owner is still alive but older than this belongs to a build
# that hung. Generous: the owner-alive check is the real staleness test.
STALE_LOCK_SECONDS = 1800


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
        "Build log: %s\n"
        "Build the environment by hand, which is not subject to the client's\n"
        "startup timeout:\n"
        '  rmdir /s /q "%s"\n'
        '  "%s" -m venv "%s"\n'
        '  "%s" install %s'
        % (reason, BUILD_LOG, VENV_DIR, sys.executable, VENV_DIR, pip_exe, requirements)
    )


# --- lock -------------------------------------------------------------------

def _pid_alive(pid):
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _lock_owner():
    """PID recorded in the lock, or 0 if it cannot be read."""
    try:
        return int(LOCK.read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        return 0


def _lock_is_live():
    """True while a builder that wrote the lock is still running."""
    try:
        age = time.time() - LOCK.stat().st_mtime
    except OSError:
        return False
    return age < STALE_LOCK_SECONDS and _pid_alive(_lock_owner())


def _acquire_lock():
    """True if this process now owns the build, False if a live builder does."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _lock_is_live():
                return False
            # The owner is gone (killed mid-build) or hung. Take over.
            try:
                LOCK.unlink()
            except OSError:
                return False
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        return True
    return False


def _release_lock():
    try:
        LOCK.unlink()
    except OSError:
        pass


# --- builder ----------------------------------------------------------------

def _build_venv():
    # An existing tree here is a partial build or one whose requirements moved.
    # Either way it cannot be trusted. Removing it first is what stops
    # `python -m venv` failing with WinError 183 on a half-created directory.
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        if VENV_DIR.exists():
            raise RuntimeError(
                "Could not remove the existing virtual environment at %s. "
                "Something is holding it open." % VENV_DIR
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


def build_main():
    """Entry point of the detached builder process."""
    if venv_is_ready():
        return 0
    if not _acquire_lock():
        print("another builder is running; nothing to do")
        return 0
    try:
        started = time.time()
        print("building %s" % VENV_DIR)
        _build_venv()
        print("ready in %.0f s" % (time.time() - started))
        return 0
    except Exception as exc:  # noqa: BLE001 - anything here must reach the log
        print("BUILD FAILED: %s" % exc)
        return 1
    finally:
        _release_lock()
        sys.stdout.flush()


def _spawn_builder():
    """Start the builder detached from this process, its console and its job."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--build"]
    log = open(BUILD_LOG, "a", encoding="utf-8", errors="replace")
    log.write("\n=== %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log.flush()
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                  close_fds=True)
    if os.name == "nt":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        try:
            subprocess.Popen(cmd, creationflags=flags, **kwargs)
        except OSError:
            # Breakaway refused by the parent's job object; the builder can
            # still outlive the launcher, just not a job-wide kill.
            flags &= ~subprocess.CREATE_BREAKAWAY_FROM_JOB
            subprocess.Popen(cmd, creationflags=flags, **kwargs)
    else:
        subprocess.Popen(cmd, start_new_session=True, **kwargs)
    log.close()


def _build_log_tail(lines=12):
    try:
        return "\n".join(BUILD_LOG.read_text(encoding="utf-8", errors="replace")
                         .splitlines()[-lines:])
    except OSError:
        return "(no build log)"


# --- launcher ---------------------------------------------------------------

def ensure_venv():
    if venv_is_ready():
        return str(PYTHON_EXE)

    if not _lock_is_live():
        _spawn_builder()

    started = time.time()
    seen_builder = False
    while time.time() - started < LAUNCH_WAIT_SECONDS:
        time.sleep(0.5)
        if venv_is_ready():
            return str(PYTHON_EXE)
        if _lock_is_live():
            seen_builder = True
        elif seen_builder or time.time() - started > 5:
            # A builder ran (or never managed to start) and there is still no
            # marker: the build failed. Say so now rather than sit out the
            # deadline.
            raise RuntimeError(_manual_build_message(
                "Building the environment failed.\n%s" % _build_log_tail()))

    raise RuntimeError(_manual_build_message(
        "The virtual environment at %s is still being built (slow network?). "
        "The build continues in the background; restart the extension in a "
        "minute." % VENV_DIR))


if __name__ == "__main__":
    if "--build" in sys.argv[1:]:
        sys.exit(build_main())
    venv_python = ensure_venv()
    server_path = str(SCRIPT_DIR / "server" / "main.py")
    # main.py keeps config.json in the same per-user folder; hand it the
    # resolved location so the two can never disagree.
    env = dict(os.environ, ALTIUM_MCP_HOME=str(DATA_DIR))
    sys.exit(subprocess.call([venv_python, server_path], env=env))
