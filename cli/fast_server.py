"""Warm CLI server for marzban-cli.

Imports the whole CLI once, then serves every `marzban-cli ...` invocation by
fork()-ing a child that runs the command directly on the caller's terminal
(stdin/stdout/stderr file descriptors are passed over a unix socket with
SCM_RIGHTS). This turns a ~2.5s cold start into a few dozen milliseconds.

The server itself is single-threaded and never touches the database, so fork()
is safe. It restarts itself when the code/config on disk changes; clients fall
back to the normal (slow) path whenever the server is missing or stale.

Started automatically by marzban-cli.py; run manually with `python -m cli.fast_server`.
"""
import fcntl
import gc
import importlib.util
import io
import json
import os
import signal
import socket
import struct
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
os.environ["MARZBAN_CLI"] = "1"
os.environ["MARZBAN_CLI_SERVER"] = "1"

SOCK = os.environ.get("MARZBAN_CLI_SOCKET", "/var/lib/marzban/cli.sock")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def source_stamp(extra_files):
    """Newest mtime of everything the CLI depends on (code, .env, xray config)."""
    newest = 0
    for base in ("app", "cli", "xray_api"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, base)):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "dashboard", "node_modules")]
            for name in filenames:
                if name.endswith(".py"):
                    try:
                        newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime_ns)
                    except OSError:
                        pass
    for path in extra_files:
        try:
            newest = max(newest, os.stat(path).st_mtime_ns)
        except OSError:
            pass
    return newest


def main():
    lock = open(SOCK + ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return  # another server is already running/starting

    # Load the whole CLI once (this is the slow part we want to pay only once).
    spec = importlib.util.spec_from_file_location("marzban_cli_main", os.path.join(ROOT, "marzban-cli.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import typer  # noqa
    from config import XRAY_JSON

    extra = [os.path.join(ROOT, f) for f in ("config.py", "marzban-cli.py", ".env")] + [XRAY_JSON]
    stamp = source_stamp(extra)

    try:
        os.unlink(SOCK)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o600)
    srv.listen(16)
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)  # auto-reap children
    log(f"cli server ready on {SOCK} (pid {os.getpid()})")

    while True:
        conn, _ = srv.accept()
        try:
            uid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[1]
            if uid != os.getuid():
                conn.close()
                continue
            msg, fds, _flags, _addr = socket.recv_fds(conn, 1 << 20, 3)
            if len(fds) != 3:
                for fd in fds:
                    os.close(fd)
                conn.close()
                continue

            if source_stamp(extra) != stamp:
                # Code/config changed: let this client use the slow path and re-exec ourselves.
                conn.send(b"S")
                for fd in fds:
                    os.close(fd)
                conn.close()
                srv.close()
                try:
                    os.unlink(SOCK)
                except OSError:
                    pass
                log("sources changed, restarting")
                os.execv(sys.executable, [sys.executable, "-m", "cli.fast_server"])

            req = json.loads(msg.decode("utf-8", "surrogateescape"))
            pid = os.fork()
            if pid:
                conn.close()
                for fd in fds:
                    os.close(fd)
                continue
            # ---- child ----
            code = 1
            try:
                srv.close()
                lock.close()
                code = run_child(mod, typer, conn, fds, req)
            except BaseException:
                try:
                    traceback.print_exc()
                except Exception:
                    pass
            finally:
                try:
                    conn.send(b"X%d" % code)
                except Exception:
                    pass
                os._exit(code)
        except Exception:
            log(traceback.format_exc())
            try:
                conn.close()
            except Exception:
                pass


def run_child(mod, typer, conn, fds, req):
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    conn.send(b"P%d" % os.getpid())

    for target, fd in zip((0, 1, 2), fds):
        os.dup2(fd, target)
    for fd in fds:
        if fd > 2:
            os.close(fd)

    out_tty = os.isatty(1)
    sys.stdin = sys.__stdin__ = os.fdopen(0, "r", encoding="utf-8", errors="replace", closefd=False)
    sys.stdout = sys.__stdout__ = os.fdopen(
        1, "w", encoding="utf-8", errors="replace", buffering=1 if out_tty else -1, closefd=False
    )
    sys.stderr = sys.__stderr__ = os.fdopen(2, "w", encoding="utf-8", errors="backslashreplace", buffering=1, closefd=False)

    os.environ.clear()
    os.environ.update(req["env"])
    try:
        os.chdir(req["cwd"])
    except OSError:
        pass
    sys.argv = [req["argv0"]] + req["argv"]

    # State computed at import time (when stdio was /dev/null) must be re-detected.
    import pydoc

    pydoc._pager = None
    try:
        from rich.console import Console

        for obj in gc.get_objects():
            if isinstance(obj, Console):
                obj._color_system = obj._detect_color_system()
    except Exception:
        pass
    try:
        from app.db import engine

        engine.dispose(close=False)  # never reuse pooled connections inherited from the parent
    except Exception:
        pass

    code = 0
    try:
        typer.completion.completion_init()
        mod.app(prog_name=os.environ.get("CLI_PROG_NAME"))
    except SystemExit as e:
        if e.code is None:
            code = 0
        elif isinstance(e.code, int):
            code = e.code
        else:
            print(e.code, file=sys.stderr)
            code = 1
    except KeyboardInterrupt:
        code = 130
    except BaseException:
        traceback.print_exc()
        code = 1
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    return code


if __name__ == "__main__":
    main()
