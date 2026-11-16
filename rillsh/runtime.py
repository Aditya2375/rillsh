"""Linux fork/exec engine. Commands are trusted, not sandboxed."""

import errno
import fcntl
import os
import re
import signal
from .language import NAME, ShellError, parse, prepare

BUILTINS = {"cd", "pwd", "export", "unset", "exit", "true", "false"}


def write(fd, text):
    data = text.encode("utf-8", errors="replace")
    while data:
        n = os.write(fd, data)
        data = data[n:]


def diagnostic(text):
    try:
        write(2, "rillsh: " + text + "\n")
    except OSError:
        pass


def apply(redirections):
    for r in redirections:
        fd, op, target = r["fd"], r["op"], r["target"]
        if op in {">&", "<&"}:
            if target == "-":
                try:
                    os.close(fd)
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        raise
            else:
                os.dup2(int(target), fd, inheritable=True)
        else:
            flags = (
                os.O_RDONLY
                if op == "<"
                else os.O_WRONLY
                | os.O_CREAT
                | (os.O_APPEND if op == ">>" else os.O_TRUNC)
            )
            opened = os.open(target, flags, 0o666)
            try:
                if opened == fd:
                    os.set_inheritable(fd, True)
                else:
                    os.dup2(opened, fd, inheritable=True)
            finally:
                if opened != fd:
                    os.close(opened)


class Engine:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        self.env["PWD"] = os.getcwd()
        self.status = 0
        self.exiting = False
        self.pid = os.getpid()

    def builtin(self, argv):
        cmd, args = argv[0], argv[1:]
        if cmd in {"true", "false"}:
            return 0 if cmd == "true" else 1
        if cmd == "pwd":
            if args:
                raise ShellError("pwd takes no arguments")
            write(1, os.getcwd() + "\n")
        elif cmd == "cd":
            if len(args) > 1:
                raise ShellError("cd takes at most one argument")
            target = args[0] if args else self.env.get("HOME")
            if target is None:
                raise ShellError("HOME is not set")
            if target == "-":
                target = self.env.get("OLDPWD")
                if target is None:
                    raise ShellError("OLDPWD is not set")
            old = os.getcwd()
            os.chdir(target)
            try:
                self.env["PWD"] = os.getcwd()
            except OSError:
                os.chdir(old)
                raise
            self.env["OLDPWD"] = old
            if args == ["-"]:
                write(1, self.env["PWD"] + "\n")
        elif cmd in {"export", "unset"}:
            if not args:
                raise ShellError(cmd + " requires arguments")
            names = [a.split("=", 1)[0] if cmd == "export" else a for a in args]
            if any(not NAME.fullmatch(n) for n in names):
                raise ShellError("invalid variable name")
            for name, arg in zip(names, args):
                if cmd == "unset":
                    self.env.pop(name, None)
                else:
                    self.env[name] = (
                        arg.split("=", 1)[1] if "=" in arg else self.env.get(name, "")
                    )
        elif cmd == "exit":
            if len(args) > 1 or (
                args and not re.fullmatch(r"[+-]?[0-9]{1,32}", args[0])
            ):
                raise ShellError("exit takes one bounded integer")
            self.exiting = True
            return (int(args[0]) & 255) if args else self.status
        return 0

    def in_parent(self, stage):
        saved = []
        try:
            for fd in range(3):
                try:
                    saved.append(fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10))
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        raise
                    saved.append(None)
            apply(stage["redirections"])
            return self.builtin(stage["argv"])
        except (OSError, ShellError) as exc:
            diagnostic(str(exc))
            return 1
        finally:
            for fd, backup in enumerate(saved):
                if backup is None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                else:
                    os.dup2(backup, fd, inheritable=True)
                    os.close(backup)

    def pipeline(self, stages):
        ready = [prepare(s, self.env, self.status, self.pid) for s in stages]
        if len(ready) == 1 and ready[0]["argv"][0] in BUILTINS:
            return self.in_parent(ready[0])
        children, pipes, pidfds = [], [], {}
        previous_handlers = {}
        interrupted = 0

        def interrupt(signum, _frame):
            nonlocal interrupted
            interrupted = signum
            for handle in list(pidfds.values()):
                try:
                    signal.pidfd_send_signal(handle, signum)
                except ProcessLookupError:
                    pass

        # Parent stays in the caller's foreground group. No job control.
        # Block spawn-time INT/QUIT so no handler can unwind a half-built pipe.
        blocked = {signal.SIGINT, signal.SIGQUIT}
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
        try:
            for sig in blocked:
                previous_handlers[sig] = signal.signal(sig, interrupt)
            for _ in range(len(ready) - 1):
                pipes.append(list(os.pipe()))
                for j, fd in enumerate(pipes[-1]):
                    if fd < 3:
                        moved = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
                        os.close(fd)
                        pipes[-1][j] = moved
            for i, stage in enumerate(ready):
                pid = os.fork()
                if pid == 0:
                    try:
                        for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGPIPE):
                            signal.signal(sig, signal.SIG_DFL)
                        signal.signal(signal.SIGTSTP, signal.SIG_IGN)
                        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
                        if i:
                            os.dup2(pipes[i - 1][0], 0)
                        if i < len(ready) - 1:
                            os.dup2(pipes[i][1], 1)
                        for ends in pipes:
                            for fd in ends:
                                os.close(fd)
                        try:
                            apply(stage["redirections"])
                        except OSError as exc:
                            diagnostic(str(exc))
                            os._exit(1)
                        # Never retain unrelated descriptors in any child.
                        for item in os.listdir("/proc/self/fd"):
                            fd = int(item)
                            if fd > 2:
                                try:
                                    os.close(fd)
                                except OSError as exc:
                                    if exc.errno != errno.EBADF:
                                        raise
                        if stage["argv"][0] in BUILTINS:
                            try:
                                os._exit(self.builtin(stage["argv"]))
                            except (OSError, ShellError) as exc:
                                diagnostic(str(exc))
                                os._exit(1)
                        os.execvpe(stage["argv"][0], stage["argv"], self.env)
                    except FileNotFoundError as exc:
                        diagnostic(str(exc))
                        os._exit(127)
                    except (OSError, ValueError) as exc:
                        diagnostic(str(exc))
                        os._exit(
                            126
                            if isinstance(exc, OSError)
                            and exc.errno in {errno.EACCES, errno.ENOEXEC, errno.EISDIR}
                            else 1
                        )
                    except BaseException:
                        diagnostic("child setup failed")
                        os._exit(1)
                children.append(pid)
                pidfds[pid] = os.pidfd_open(pid)
        except BaseException:
            for pid in children:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise
        finally:
            for ends in pipes:
                for fd in ends:
                    os.close(fd)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            # Reap every child, including partial-spawn failure paths.
            statuses = {}
            for pid in children:
                while True:
                    try:
                        _, value = os.waitpid(pid, 0)
                        code = os.waitstatus_to_exitcode(value)
                        statuses[pid] = code if code >= 0 else 128 - code
                        handle = pidfds.pop(pid, None)
                        if handle is not None:
                            os.close(handle)
                        break
                    except InterruptedError:
                        continue
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
        return 128 + interrupted if interrupted else statuses[children[-1]]

    def execute(self, source):
        try:
            ast = parse(source)  # Validate the whole input before side effects.
        except ShellError as exc:
            diagnostic(str(exc))
            self.status = 2
            return self.status
        for pipe in ast["pipelines"]:
            if pipe["gate"] == "success" and self.status != 0:
                continue
            if pipe["gate"] == "failure" and self.status == 0:
                continue
            try:
                self.status = self.pipeline(pipe["stages"])
            except (OSError, ShellError) as exc:
                diagnostic(str(exc))
                self.status = 1
            except KeyboardInterrupt:
                diagnostic("interrupted")
                self.status = 130
            if self.exiting:
                break
        return self.status
