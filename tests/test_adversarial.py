import errno
import fcntl
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from rillsh.language import ShellError, parse, plan
from rillsh.runtime import Engine
from tests.test_runtime import ROOT, helper, run


class Adversarial(unittest.TestCase):
    def test_huge_io_number_no_integer_conversion_crash(self):
        with self.assertRaises(ShellError):
            parse("x " + "9" * 6000 + ">f")
        self.assertEqual(
            plan("x " + "0" * 6000 + "2>f", {})["pipelines"][0]["stages"][0][
                "redirections"
            ][0]["fd"],
            2,
        )

    def test_descriptor_close_error_and_restore(self):
        self.assertEqual(run("pwd 0<&- 1>&0; printf ok").stdout, "ok")
        with self.assertRaises(ShellError):
            plan("pwd 0<&- 1>&0", {})
        self.assertEqual(run("printf ok 1>&1").stdout, "ok")
        self.assertEqual(run("pwd 1>&-; printf ok").stdout, "ok")

    def test_same_path_open_identity_not_dup(self):
        a = plan("x >out 2>out", {})["pipelines"][0]["stages"][0]
        self.assertNotEqual(a["fds"][1], a["fds"][2])
        b = plan("x >out 2>&1", {})["pipelines"][0]["stages"][0]
        self.assertEqual(b["fds"][1], b["fds"][2])
        with tempfile.TemporaryDirectory() as d:
            cmd = helper('import os;os.write(1,b"ABCDE");os.write(2,b"xy")')
            run(cmd + " >out 2>out", cwd=d)
            self.assertEqual((Path(d) / "out").read_text(), "xyCDE")
            run(cmd + " >out 2>&1", cwd=d)
            self.assertEqual((Path(d) / "out").read_text(), "ABCDExy")

    def test_order_failure_has_explicit_partial_file_effect(self):
        with tempfile.TemporaryDirectory() as d:
            r = run("printf x >created <missing", cwd=d)
            self.assertEqual(r.returncode, 1)
            self.assertTrue((Path(d) / "created").exists())
            self.assertEqual((Path(d) / "created").read_bytes(), b"")

    def test_expansion_never_reparses(self):
        with tempfile.TemporaryDirectory() as d:
            r = run('printf "%s" "$X"', env={"X": "; touch OWNED | $(bad)"}, cwd=d)
            self.assertEqual(r.stdout, "; touch OWNED | $(bad)")
            self.assertFalse((Path(d) / "OWNED").exists())

    def test_pipe_allocation_failure_closes_previous(self):
        engine = Engine()
        before = set(os.listdir("/proc/self/fd"))
        original = os.pipe
        calls = 0

        def failure():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(errno.EMFILE, "injected pipe failure")
            return original()

        with patch("os.pipe", side_effect=failure):
            self.assertEqual(engine.execute("true | true | true"), 1)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)

    def test_pidfd_failure_kills_and_reaps(self):
        engine = Engine()
        before = set(os.listdir("/proc/self/fd"))
        with patch(
            "os.pidfd_open", side_effect=OSError(errno.EMFILE, "injected pidfd failure")
        ):
            self.assertEqual(engine.execute("sleep 5 | true"), 1)
        self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        with self.assertRaises(ChildProcessError):
            os.waitpid(-1, os.WNOHANG)

    def test_closed_stderr_is_preserved(self):
        source = "pwd 2>&-; " + helper(
            'import os;print(os.path.exists("/proc/self/fd/2"))'
        )
        p = subprocess.run(
            [sys.executable, "-m", "rillsh", "-c", source],
            cwd=ROOT,
            preexec_fn=lambda: os.close(2),
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertTrue(p.stdout.endswith("False\n"))
        self.assertEqual(p.returncode, 0)

    def test_unterminated_stdin_and_boundary(self):
        r = subprocess.run(
            [sys.executable, "-m", "rillsh"],
            cwd=ROOT,
            input="printf hi",
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(r.stdout, "hi")
        r = subprocess.run(
            [sys.executable, "-m", "rillsh"],
            cwd=ROOT,
            input="x" * 8193,
            text=True,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(r.returncode, 2)

    def test_parameter_and_redirection_at_exact_limits(self):
        self.assertEqual(
            plan("echo $X", {"X": "x" * 65536})["pipelines"][0]["stages"][0]["argv"][1],
            "x" * 65536,
        )
        with self.assertRaises(ShellError):
            plan("echo $X", {"X": "é" * 32769})
        self.assertEqual(
            len(parse("x " + ">f " * 16)["pipelines"][0]["stages"][0]["redirections"]),
            16,
        )
        for source in [
            "x " * 129,
            "x " + ">f " * 17,
            ";".join("x" for _ in range(33)),
            "x " + "a " * 129,
        ]:
            with self.assertRaises(ShellError):
                parse(source)

    def test_export_validation_is_all_or_nothing(self):
        r = run('export X=old; export X=new 1INVALID=bad; printf "%s" "$X"')
        self.assertEqual(r.stdout, "old")
        r = run('export X=old; unset X 1INVALID; printf "%s" "$X"')
        self.assertEqual(r.stdout, "old")

    def test_cli_empty_expansion_command(self):
        self.assertEqual(run("$NOT_SET", env={"NOT_SET": ""}).returncode, 1)
        self.assertEqual(
            run("exit 999999999999999999999999999999999999999").returncode, 1
        )


class Encoding(unittest.TestCase):
    def test_unicode_environment_and_invalid_utf8(self):
        self.assertEqual(
            plan("echo $X", {"X": "🙂"})["pipelines"][0]["stages"][0]["argv"][1], "🙂"
        )
        with self.assertRaises(ShellError):
            plan("echo $X", {"X": "\udcff"})


class ChildBuiltinErrors(unittest.TestCase):
    def test_child_builtin_os_errors_are_not_exec_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            missing = shlex.quote(str(Path(d) / "missing"))
            self.assertEqual(run("true | cd " + missing).returncode, 1)
            self.assertEqual(run("true | export 1BAD=x").returncode, 1)


class SuspendPolicy(unittest.TestCase):
    def test_non_orphan_group_c_and_stdin_ignore_tstp(self):
        # The supervisor is a session leader in a different group from the shell.
        # Therefore the shell's group is not orphaned, and SIGTSTP would really
        # stop an unprotected parent rather than being discarded by Linux.
        supervisor = r"""
import json,os,signal,subprocess,sys,time
mode=sys.argv[1]
source='sleep .5; printf survived'
argv=[sys.executable,'-m','rillsh']+(['-c',source]if mode=='c'else[])
p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,preexec_fn=os.setpgrp)
try:
 if mode=='stdin':p.stdin.write(source.encode());p.stdin.flush();p.stdin.close();p.stdin=None
 time.sleep(.18)
 os.killpg(p.pid,signal.SIGTSTP)
 time.sleep(.08)
 got,status=os.waitpid(p.pid,os.WNOHANG|os.WUNTRACED)
 stopped=bool(got and os.WIFSTOPPED(status))
 if stopped:os.killpg(p.pid,signal.SIGCONT)
 out,err=p.communicate(timeout=3)
 print(json.dumps({'stopped':stopped,'early_wait_pid':got,'out':out.decode(),'code':p.returncode}))
finally:
 if p.poll()is None:os.killpg(p.pid,signal.SIGKILL);p.wait()
"""
        for mode in ["c", "stdin"]:
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, "-c", supervisor, mode],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    start_new_session=True,
                    timeout=5,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                import json

                record = json.loads(result.stdout)
                self.assertFalse(record["stopped"], record)
                self.assertEqual(record["early_wait_pid"], 0, record)
                self.assertEqual(record["out"], "survived", record)
                self.assertEqual(record["code"], 0, record)
