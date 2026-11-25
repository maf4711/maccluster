"""Stand-in for ssh in tests: runs the "remote" command on this Mac, never touches the network.

    RemoteTransport.ssh_cmd = [sys.executable, "tests/fake_ssh.py", "--state", "<peer state dir>"]

Usage as ssh: fake_ssh.py [--state DIR] [ssh options] userhost command...  The command runs under
/bin/sh with `python3` bound to /usr/bin/python3 (3.9, like the peers) when the PATH has a python3,
and DOCSYNC_STATE_DIR set to DIR, so one test can play two Macs with separate state dirs. The process execs into the shell, so
the command inherits stdin/stdout directly (EOF on stdin reaches it like through a real ssh)."""
import os
import sys

WITH_ARG = {"-o", "-b", "-l", "-p", "-i", "-F", "-c", "-e", "-m"}


def main(argv: list[str]) -> None:
    env = dict(os.environ)
    if argv[:1] == ["--state"]:
        env["DOCSYNC_STATE_DIR"] = argv[1]
        argv = argv[2:]
    i = 0
    while i < len(argv) and argv[i].startswith("-"):
        i += 2 if argv[i] in WITH_ARG else 1
    if i + 1 >= len(argv):
        sys.exit("fake_ssh: usage: fake_ssh.py [--state DIR] [options] host command")
    command = " ".join(argv[i + 1:])
    # bind python3 only where the PATH has one, so a test can play a peer without python3 (PATH=/bin)
    shim = 'command -v python3 >/dev/null 2>&1 && python3() { /usr/bin/python3 "$@"; }\n'
    os.execve("/bin/sh", ["sh", "-c", shim + command], env)


if __name__ == "__main__":
    main(sys.argv[1:])
