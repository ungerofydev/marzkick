#!/usr/bin/env python3
import os  # noqa
import sys  # noqa


def _fast_cli():
    """Forward this call to the warm CLI server (cli/fast_server.py) if it is running.

    Only stdlib is imported here on purpose: this runs before the heavy imports below.
    Any problem (no server, stale server, no socket dir) falls through to the normal path.
    Set MARZBAN_CLI_NO_FAST=1 to disable.
    """
    if os.environ.get("MARZBAN_CLI_NO_FAST") or os.environ.get("MARZBAN_CLI_SERVER"):
        return
    path = os.environ.get("MARZBAN_CLI_SOCKET", "/var/lib/marzban/cli.sock")
    if not os.path.isdir(os.path.dirname(path)):
        return
    import json
    import signal
    import socket

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        sock.connect(path)
        msg = json.dumps(
            {"argv0": sys.argv[0], "argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()}
        ).encode("utf-8", "surrogateescape")
        if len(msg) > 200000:
            return
        socket.send_fds(sock, [msg], [0, 1, 2])
    except OSError:
        sock.close()
        _spawn_fast_server(path)
        return

    child = None
    code = None

    def forward(signum, _frame):
        if child:
            try:
                os.kill(child, signum)
            except OSError:
                pass

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    while True:
        try:
            data = sock.recv(256)
        except OSError:
            break
        if not data:
            break
        if data[:1] == b"P":
            child = int(data[1:])
        elif data[:1] == b"X":
            code = int(data[1:])
            break
        elif data[:1] == b"S":  # server is stale/restarting -> use the slow path this time
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            return
    os._exit(code if code is not None else 1)


def _spawn_fast_server(path):
    """Start the warm server in the background (once); this call still runs the slow path."""
    try:
        import fcntl
        import subprocess

        with open(path + ".lock", "a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return  # a server is already starting
            fcntl.flock(lock, fcntl.LOCK_UN)
        log = open(path + ".log", "ab")
        subprocess.Popen(
            [sys.executable, "-m", "cli.fast_server"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env={**os.environ, "MARZBAN_CLI_SERVER": "1"},
        )
    except Exception:
        pass


_fast_cli()

import readline  # noqa

sys.path.insert(0, os.getcwd())  # noqa
os.environ.setdefault("MARZBAN_CLI", "1")  # noqa: CLI does not need the web stack

import typer
from typer._completion_shared import Shells

import cli.admin
import cli.subscription
import cli.user

app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(cli.admin.app, name="admin")
app.add_typer(cli.subscription.app, name="subscription")
app.add_typer(cli.user.app, name="user")


# Hidden completion app
app_completion = typer.Typer(no_args_is_help=True, help="Generate and install completion scripts.", hidden=True)
app.add_typer(app_completion, name="completion")


def get_default_shell() -> Shells:
    shell = os.environ.get('SHELL')
    if shell:
        shell = shell.split('/')[-1]
        if shell in Shells.__members__:
            return getattr(Shells, shell)
    return Shells.bash


@app_completion.command(help="Show completion for the specified shell, to copy or customize it.")
def show(ctx: typer.Context, shell: Shells = typer.Option(None,
                                                          help="The shell to install completion for.",
                                                          case_sensitive=False)) -> None:
    if shell is None:
        shell = get_default_shell()
    typer.completion.show_callback(ctx, None, shell)


@app_completion.command(help="Install completion for the specified shell.")
def install(ctx: typer.Context, shell: Shells = typer.Option(None,
                                                             help="The shell to install completion for.",
                                                             case_sensitive=False)) -> None:
    if shell is None:
        shell = get_default_shell()
    typer.completion.install_callback(ctx, None, shell)


if __name__ == "__main__":
    typer.completion.completion_init()
    app(prog_name=os.environ.get('CLI_PROG_NAME'))
