"""Entry point: interactive prompt, -c execution, or a side-effect-free plan."""

import argparse
import json
import os
import signal
import sys
from .language import ShellError, plan
from .runtime import Engine, diagnostic


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Bounded Linux shell. Trusted commands only; not POSIX sh."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-c", metavar="SOURCE")
    group.add_argument(
        "--plan",
        metavar="SOURCE",
        help="parse and preview without executing or opening files",
    )
    parser.add_argument("--version", action="version", version="rillsh 1.0.0")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        diagnostic("Linux is required")
        return 2
    if os.getuid() == 0 or os.getuid() != os.geteuid() or os.getgid() != os.getegid():
        diagnostic("root and set-ID execution are refused")
        return 2
    if args.plan is not None:
        try:
            print(
                json.dumps(plan(args.plan, dict(os.environ), 0, os.getpid()), indent=2)
            )
            return 0
        except ShellError as exc:
            diagnostic(str(exc))
            return 2
    # Apply the no-suspend policy in every execution mode, before dispatch.
    signal.signal(signal.SIGTSTP, signal.SIG_IGN)
    engine = Engine()
    if args.c is not None:
        return engine.execute(args.c)
    if not sys.stdin.isatty():
        source = sys.stdin.read(8193)
        return engine.execute(source)
    # No process groups, background jobs, suspend/resume, rc files or history.
    signal.signal(signal.SIGQUIT, signal.SIG_IGN)
    while not engine.exiting:
        try:
            source = input("rill> ")
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print()
            engine.status = 130
            continue
        engine.execute(source)
    return engine.status


if __name__ == "__main__":
    raise SystemExit(main())
