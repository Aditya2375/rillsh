# rillsh

A bounded Linux command shell with real concurrent pipelines and a browser workbench that makes its language visible.

The native CLI forks and executes commands, wires pipes, applies ordered descriptor redirections, preserves parent builtin state, short-circuits lists and reaps every child. The browser runs an independent parser and planner. It does not execute commands or open files.

## Run

Requires Linux 5.3+ with mounted `/proc` and Python 3.10+. No runtime dependencies. Run as a normal user, not root or set-ID. This is a single-threaded CLI, not an embedding API.

```sh
python3 -m rillsh
python3 -m rillsh -c 'printf "%s\n" "hello" | tr a-z A-Z | wc -c'
python3 -m rillsh -c 'false && printf skipped || printf recovered'
python3 -m rillsh --plan 'printf hello | wc -c >report.txt 2>&1'
```

`--plan` parses, expands using the current environment, initial status 0 and the current process ID, and prints symbolic descriptors without running commands or opening target files. It previews all branches, including branches that execution might skip. Later pipelines in a plan still use sample status 0; execution instead uses the actual previous status. Planning does not verify command availability, file permissions or runtime outcomes. Do not paste secrets into any command you intend to share.

With no arguments and a TTY, the shell shows `rill> `. Each entered line is one complete input. There is no continuation prompt. Without a TTY it reads at most 8,193 source characters from stdin, rejects oversized input, then executes the complete input. Reading source from stdin consumes that input before commands start; use `-c` when a command must read stdin data. There is no script-file argument or positional parameter support.

The shell itself changes only the requested working directory and environment in memory, opens explicit redirection targets and starts commands. It loads no rc files and saves no history. Started commands can do anything your account can do, including network access, deletion and spending. **This is not a sandbox.** Use trusted commands, PATH entries and environment values. Redirection `>` can truncate an existing file. File modes use the caller's umask. Symlinks, devices and FIFOs have ordinary OS semantics and can block or affect other files. There is no execution timeout or output cap.

## Language

This is deliberately **not POSIX sh**. Do not replace `/bin/sh`, use it as a login shell, or expect existing shell scripts to work unchanged.

- Printable ASCII, tab and newline source. Single quotes preserve literal text. Double quotes permit parameter expansion and shell-style backslash escaping of `$`, backtick, `"` and `\`. Backslash-newline is removed outside single quotes. Quotes concatenate with adjacent text and preserve empty words.
- `$NAME`, `${NAME}`, `$?` and `$$`. Environment names use ASCII letters, digits and underscore, and cannot begin with a digit. Expansion never creates new syntax. There is **no field splitting**: each lexical word becomes exactly one argument, even when an unquoted variable is unset or contains spaces. There is no glob or tilde expansion. Quote literal glob, tilde and brace characters.
- `|` pipelines, `;` or newline lists, and left-associative `&&` / `||` with equal precedence. Conditional operators require the next pipeline immediately, not a newline. Empty separators are ignored. Comments begin with `#` at the start of a token. Inside a word, `#` is literal.
- Redirections `[n]<word`, `[n]>word`, `[n]>>word`, `[n]<&word`, `[n]>&word`. Only descriptors 0, 1 and 2 are supported. Duplication targets expand to `0`, `1`, `2` or `-` for close. The IO number must be unquoted and adjacent: `2>f` redirects stderr, while `"2">f` passes argument `2` and redirects stdout. Expansion of redirection words is the same one-word expansion used for arguments.
- Pipes are assigned before redirections. Redirections apply left to right. `tool 2>&1 >out` leaves stderr on the previous stdout; `tool >out 2>&1` sends both streams to the opened file. The workbench labels each file open with a distinct `open:N` identity, so repeated opens of the same path are not confused with descriptor duplication. These are symbolic handles, not actual file descriptions.
- No command or arithmetic substitution, heredocs, background jobs, process substitution, grouping, functions, assignment prefixes, control structures, aliases, globbing or positional parameters. Unsupported syntax is rejected rather than delegated to another shell. To run a shell script, explicitly name a trusted interpreter. An executable text file without a shebang is not automatically retried through `/bin/sh`.

The entire input is lexed and parsed before any command starts. Syntax errors cannot execute an earlier valid prefix. Expansion and descriptor-target validation occur per pipeline just before it starts; an expansion failure does not undo earlier successful pipelines. Redirection opens occur at execution, in order: a later failure can leave an earlier file created or truncated, just as ordinary ordered redirection would. Pipelines are not transactions.

## Builtins and status

`cd [path]` uses HOME if omitted; `cd -` uses OLDPWD and prints the new physical path. `pwd` takes no options and prints the physical working directory. `export NAME[=value]...` requires arguments, exports every listed variable, and makes an unset name empty. `unset NAME...` requires arguments. Each validates all names before updating any variable. `true` and `false` return 0 and 1. `exit [integer]` uses the previous status if omitted, otherwise the bounded integer modulo 256. Invalid builtin arguments return 1 without exiting.

A single builtin runs in the parent with standard descriptors saved and restored. In a multi-stage pipeline, all builtins run in children; `cd`, `export`, `unset` and `exit` cannot change the parent. All pipeline stages start before waiting, and every child is reaped, even on partial spawn failure. The pipeline status is the last stage's status; there is no pipefail option. A killed stage has status `128 + signal`. Syntax errors return 2; expansion, setup and redirection errors normally return 1. Missing executables return 127; permission/format failures return 126. PATH lookup uses Python's `execvpe` semantics: an inaccessible PATH entry can result in 126 even when the name was not found elsewhere. Use a trusted, accessible PATH. An empty expanded command name is a setup error.

No job control: children remain in the caller's foreground process group, and Ctrl-Z is ignored by the shell and its children. The shell catches INT/QUIT while waiting and forwards them to direct children using Linux pidfds, avoiding recycled-PID targets. Terminal-generated signals already reach the whole foreground group. Directly signalling only the shell does not promise to signal grandchildren. Ctrl-C at the prompt clears the line and sets status 130. An interrupt during a pipeline returns `128 + signal`; subsequent list commands may still run. SIGKILL, SIGSTOP, externally stopped processes, programs that ignore signals, daemonizing commands and fatal parent termination are not managed. This is not a process supervisor.

## Bounds

8,192 source characters; 1,024 tokens; 32 pipelines; 16 stages per pipeline; 128 words and 16 redirections per stage; 65,536 UTF-8 bytes per expanded word. Expanded words must be valid UTF-8 and cannot contain NUL. These are parser/expansion limits, not CPU, memory, file or process quotas on executed programs. Browser sample values are capped at 256 characters and sample status at 0 through 255. Browser source input stays local to the page; no storage, analytics or network APIs are used. Static hosting can still log ordinary asset requests.

The workbench's `terminal:*` labels mean inherited standard descriptors. With `-c`, those can be files, pipes, closed descriptors or a terminal. The planner assumes initially open standard descriptors and cannot inspect native descriptors. It rejects duplication after a descriptor was symbolically closed, but other validity is checked only by the native OS. Every pipeline's sample `$?` is illustrative, not an assertion of runtime status.

## Check

```sh
make test
npm ci --ignore-scripts
npx playwright install chromium
npm test
npm run test:browser
python3 -m http.server 8000 --directory docs
```

Tests include a 1,200-case independent quote oracle, comparisons with dash for the common subset, a 2 MB pipeline, early-reader exit, real descriptor-order effects, parent/child builtin state, FD closure and leak checks, injected partial fork failure, interrupt recovery and a real PTY. Native/browser parity covers complete ASTs, arguments, descriptor maps, ordered snapshots and errors for a seeded corpus. Parity alone is not an independent semantics proof. Browser checks cover desktop/mobile input, presets, invalid and edited states, literal HTML-like values, controls, contrast and overflow. Chromium is tested; Safari, Firefox, Android and non-Linux native platforms are not.

## References and license

Original implementation and visual design. MIT OR Apache-2.0. Standards and API references informed behavior; no external implementation is bundled:

- [POSIX Shell Command Language](https://pubs.opengroup.org/onlinepubs/9799919799.2024edition/utilities/V3_chap02.html)
- [Python os](https://docs.python.org/3/library/os.html) and [signal](https://docs.python.org/3/library/signal.html)
- [Linux pipe(7)](https://man7.org/linux/man-pages/man7/pipe.7.html)
- [Virginia Tech shell FAQ](https://courses.cs.vt.edu/~cs3214/spring2015/projects/esh3-faq.php)
