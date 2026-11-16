import contextlib
import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from rillsh.runtime import Engine

ROOT = Path(__file__).resolve().parents[1]
PY = shlex.quote(sys.executable)


def run(source, **kw):
    return subprocess.run(
        [sys.executable, "-m", "rillsh", "-c", source],
        cwd=kw.pop("cwd", ROOT),
        capture_output=True,
        text=True,
        timeout=8,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "PATH": "/usr/bin:/bin",
            **kw.pop("env", {}),
        },
        **kw
    )


def helper(code):
    return PY + " -c " + shlex.quote(code)


class Runtime(unittest.TestCase):
    def test_simple_status_not_found_nonexec(self):
        self.assertEqual(run("true").returncode, 0)
        self.assertEqual(run("false").returncode, 1)
        self.assertEqual(run("command_that_cannot_exist_rillsh").returncode, 127)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f"
            p.write_text("content")
            self.assertEqual(run(shlex.quote(str(p))).returncode, 126)
        self.assertEqual(run("printf '%s' ''").stdout, "")

    def test_pipe_megabyte_and_all_reaped(self):
        src = helper('import os;os.write(1,b"x"*2000000)') + " | wc -c"
        self.assertEqual(run(src).stdout.strip(), "2000000")
        src = helper('import time;time.sleep(.15);print("done")') + " | true"
        t = time.monotonic()
        self.assertEqual(run(src).returncode, 0)
        self.assertGreater(time.monotonic() - t, 0.14)

    def test_early_consumer_sigpipe(self):
        r = run("yes | head -c 1")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "y")
        r = run(
            "true | " + helper("import os,signal;os.kill(os.getpid(),signal.SIGTERM)")
        )
        self.assertEqual(r.returncode, 143)

    def test_short_circuit_and_expansion_time(self):
        r = run(
            'false && printf bad || printf good; export X=hi; printf ":%s:%s" "$X" "$?"'
        )
        self.assertEqual(r.stdout, "good:hi:0")
        self.assertEqual(run("exit 7; printf bad").returncode, 7)
        self.assertEqual(run("exit -1").returncode, 255)
        self.assertEqual(run("exit nope; printf ok").stdout, "ok")
        self.assertEqual(run("false; exit").returncode, 1)

    def test_fd_order_real(self):
        cmd = helper('import os;os.write(1,b"OUT");os.write(2,b"ERR")')
        with tempfile.TemporaryDirectory() as d:
            r = run(cmd + " 2>&1 >f", cwd=d)
            self.assertEqual(r.stdout, "ERR")
            self.assertEqual((Path(d) / "f").read_text(), "OUT")
            r = run(cmd + " >f 2>&1", cwd=d)
            self.assertEqual(r.stdout, "")
            self.assertEqual((Path(d) / "f").read_text(), "OUTERR")
            run("printf A >f; printf B >>f", cwd=d)
            self.assertEqual((Path(d) / "f").read_text(), "AB")
            self.assertEqual(run("cat <f", cwd=d).stdout, "AB")

    def test_pipe_overridden_and_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run("printf hi >f | wc -c", cwd=d).stdout.strip(), "0")
            self.assertEqual((Path(d) / "f").read_text(), "hi")
            self.assertEqual(run("cat <missing", cwd=d).returncode, 1)
            self.assertEqual(run("true >missing/f", cwd=d).returncode, 1)

    def test_parse_all_before_effects(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(run('printf hi >f; echo "bad', cwd=d).returncode, 2)
            self.assertFalse((Path(d) / "f").exists())
            self.assertEqual(
                run("true; x >&9", cwd=d).returncode, 1
            )  # expansion validation is per pipeline

    def test_builtins_parent_child_state_and_restore(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("pwd >saved; cd " + shlex.quote(d) + "; pwd; cd -; pwd")
            self.assertEqual(r.stdout.splitlines(), [d, str(ROOT), str(ROOT)])
            (ROOT / "saved").unlink()
            r = run('export X=parent; export X=child | cat; printf "%s" "$X"')
            self.assertEqual(r.stdout, "parent")
            self.assertEqual(run("exit 4 | cat; printf alive").stdout, "alive")
            self.assertEqual(run("pwd 1>&-; printf still").stdout, "still")
            self.assertEqual(run('unset HOME; cd; printf "%s" "$?"').stdout, "1")
            self.assertEqual(
                run('export X=ok; unset X; printf "[%s]" "$X"').stdout, "[]"
            )

    def test_no_extra_exec_fds(self):
        fd = os.open("/dev/null", os.O_RDONLY)
        os.set_inheritable(fd, True)
        try:
            source = helper(
                'import os;print([n for n in os.listdir("/proc/self/fd") if int(n)>2 and os.path.exists("/proc/self/fd/"+n)])'
            )
            r = subprocess.run(
                [sys.executable, "-m", "rillsh", "-c", source],
                cwd=ROOT,
                pass_fds=(fd,),
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(r.stdout.strip(), "[]")
        finally:
            os.close(fd)

    def test_closed_standard_descriptor_pipeline(self):
        source = "printf hi | wc -c"
        r = subprocess.run(
            [sys.executable, "-m", "rillsh", "-c", source],
            cwd=ROOT,
            preexec_fn=lambda: os.close(0),
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "2")

    def test_parent_fd_leak_80_runs(self):
        engine = Engine()
        before = set(os.listdir("/proc/self/fd"))
        for _ in range(80):
            self.assertEqual(engine.execute("true | true | true"), 0)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)

    def test_fork_failure_reaps(self):
        engine = Engine()
        real = os.fork
        calls = 0

        def fail():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EAGAIN, "injected fork failure")
            return real()

        before = set(os.listdir("/proc/self/fd"))
        with patch("os.fork", side_effect=fail):
            self.assertEqual(engine.execute("sleep 5 | cat"), 1)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)

    def test_root_setid_guard_all_modes(self):
        from rillsh.__main__ import main

        for ids in [(0, 0, 1, 1), (1, 0, 1, 1), (1, 1, 1, 2)]:
            for args in [["-c", "true"], ["--plan", "true"], []]:
                with patch("os.getuid", return_value=ids[0]), patch(
                    "os.geteuid", return_value=ids[1]
                ), patch("os.getgid", return_value=ids[2]), patch(
                    "os.getegid", return_value=ids[3]
                ):
                    self.assertEqual(main(args), 2)

    def test_plan_no_files(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(
                [sys.executable, "-m", "rillsh", "--plan", "printf hi >report.txt"],
                cwd=d,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertEqual(r.returncode, 0)
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertEqual(
                json.loads(r.stdout)["pipelines"][0]["stages"][0]["fds"][1],
                "open:1:truncate:report.txt",
            )

    def test_sigint_child_and_recovery(self):
        p = subprocess.Popen(
            [sys.executable, "-m", "rillsh", "-c", 'sleep 10; printf "%s" "$?"'],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
        time.sleep(0.2)
        os.killpg(p.pid, signal.SIGINT)
        out, err = p.communicate(timeout=3)
        self.assertEqual(out, "130")
        self.assertEqual(p.returncode, 0)

    def test_differential_dash_supported_80(self):
        sources = [
            'printf "%s\\n" ' + shlex.quote(x)
            for x in ["", "a b", "$HOME", "x|y", "abc", "'", '"', "\\"]
        ]
        sources += [
            "true && printf yes",
            "false || printf yes",
            "false && printf no || printf yes",
            "printf abc | wc -c",
            'printf "%s" "$HOME"',
            'false; printf "%s" "$?"',
            "true | false",
            "false | true",
        ]
        for src in sources * 5:
            a = run(src)
            b = subprocess.run(
                ["/bin/dash", "-c", src], capture_output=True, text=True, timeout=4
            )
            self.assertEqual((a.stdout, a.returncode), (b.stdout, b.returncode), src)


if __name__ == "__main__":
    unittest.main()


class Terminal(unittest.TestCase):
    def test_actual_pty_interrupt_then_continue(self):
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(ROOT)
            os.execv(sys.executable, [sys.executable, "-m", "rillsh"])
        buffer = b""

        def until(needle, timeout=4):
            nonlocal buffer
            end = time.monotonic() + timeout
            while needle not in buffer:
                if time.monotonic() > end:
                    raise AssertionError(repr(buffer))
                if select.select([fd], [], [], 0.1)[0]:
                    buffer += os.read(fd, 4096)
            before, buffer = buffer.split(needle, 1)
            return before

        try:
            until(b"rill> ")
            os.write(fd, b"sleep 10 | cat\n")
            time.sleep(0.15)
            os.write(fd, b"\x03")
            until(b"rill> ")
            os.write(fd, b'printf "STATUS:%s\\n" "$?"\n')
            text = until(b"rill> ")
            self.assertIn(b"STATUS:130", text)
            with tempfile.TemporaryDirectory() as d:
                fifo = Path(d) / "fifo"
                os.mkfifo(fifo)
                os.write(fd, ("pwd >" + shlex.quote(str(fifo)) + "\n").encode())
                time.sleep(0.15)
                os.write(fd, b"\x03")
                until(b"rill> ")
                os.write(fd, b'printf "BUILTIN:%s\\n" "$?"\n')
                self.assertIn(b"BUILTIN:130", until(b"rill> "))
            os.write(fd, b"printf alive\n")
            self.assertIn(b"alive", until(b"rill> "))
            os.write(fd, b"exit 0\n")
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            pid = None
        finally:
            if pid:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            os.close(fd)
