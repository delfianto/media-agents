# Process supervision and cancellation

`psammophis.runtime.process.ProcessSupervisor` is the one place every long-running child process goes through -- `track-strip`'s `transcode.py`/`remux_mkv.py`/`remux_ffmpeg.py`, `compare`'s metrics/image pipelines, `mkvedit`'s runner, and `transcode`/`degrain`'s encode steps all shell out through it rather than calling `subprocess` directly, specifically so cancellation, live logging, and progress parsing behave identically everywhere instead of each caller reinventing (and subtly getting wrong) its own version.

## The cancellation contract

`cancel(sig)` sends `sig` (default `SIGTERM`) to the child's process group on POSIX so descendants spawned by the child (e.g. a shell wrapper) are reached too, not just the immediate child. `_wait_after_cancel()` then gives the child `cancellation_grace` seconds to exit on its own; if it hasn't, it escalates to `SIGKILL` and waits a second `cancellation_grace` window.

If the child *still* hasn't exited after `SIGKILL` plus that second grace period, `_wait_after_cancel()` raises `ProcessTerminationFailed` rather than blocking forever. This exists because of a real incident: a `transcode` ffmpeg child outlived its "cancelled" supervisor and kept writing to its temp output file, which a later retry against the same target then collided with. Blocking forever isn't right either (the caller needs to know something is wrong so it can surface that to the user), so the fix is a bounded wait that fails loudly instead of hanging or silently declaring success.

## A masking bug this introduced, found and fixed in review

`run()`'s top-level exception handler calls `_wait_after_cancel()` to clean up after *any* exception that interrupts the main loop (a reader-thread failure, a timeout, or a genuine `KeyboardInterrupt`/`CancellationRequested` from outside). Once `_wait_after_cancel()` could itself raise, an unguarded second call to it inside that handler created a real bug: if the *first* call to `_wait_after_cancel()` raised `ProcessTerminationFailed` (propagating it into the handler as the active exception), and the *second* call inside the handler also failed to confirm the kill, the second `ProcessTerminationFailed` silently replaced the first exception -- including, in the worst case, a genuine `KeyboardInterrupt`.

That matters because `run.py` (and other callers) pattern-match `except KeyboardInterrupt, SystemExit, CancellationRequested:` specifically to treat user-initiated cancellation differently from a generic failure -- different cleanup, different reported status ("cancelled" vs "failed"). Under the bug, exactly the scenario `ProcessTerminationFailed` exists to catch (a process that won't die) was also the one case where that type-based handling silently stopped working. Python's implicit exception chaining keeps the original exception reachable as `__context__` for tracebacks, but that's invisible to `except SomeType:` matching.

Fixed by catching `ProcessTerminationFailed` specifically around the second call and attaching it as a note (`BaseException.add_note`, Python 3.11+) to the *original* exception instead of letting it propagate and override:

```python
except BaseException as exc:
    self.cancel(requested_signal)
    try:
        self._wait_after_cancel(threads)
    except ProcessTerminationFailed as term_exc:
        exc.add_note(f"additionally, process would not terminate: {term_exc}")
    raise
```

Verified with a regression test (`test_cancellation_exception_type_survives_when_process_wont_die`) that drives this through the real `run()` method -- a real child that ignores `SIGTERM` combined with `os.killpg` mocked to a no-op, so `SIGKILL` never actually lands -- and confirmed to fail (surfacing `ProcessTerminationFailed` instead of the injected `KeyboardInterrupt`) against the pre-fix code before being confirmed to pass against the fix. The existing `test_wait_after_cancel_raises_when_kill_has_no_effect` only exercised `_wait_after_cancel()` in isolation and wouldn't have caught this -- the bug lived specifically in how `run()` composes two calls to it, not in either call alone.

## This is not the same as the chat-agent 10-hour kill

`ProcessSupervisor` and the SIGTERM → `CancellationRequested` path write
`run.completed status=cancelled` (exit 143) when they get time to finish.
That is what the Star Trek SNW S01E02 encodes did on 2026-08-09
(`Interrupted by signal 15`).

A different failure mode has no encoder error at all: the **agent harness**
`run_terminal_command` explicit timeout (maximum 36 000s). On expiry it
SIGTERMs the tool's process group and SIGKILLs ~1s later — shorter than
this supervisor's 5s grace, so `complete_run` often never runs. The journal
stays `state=running`, `exit_code=null`. ffmpeg is started with
`start_new_session=True`, so it can outlive the dead parent and keep writing
`.transcode-tmp.mkv`.

Incident: VDH GPU batch `01a00613-86da-74a9-881b-03ded30ed649`, started
2026-08-15T15:38:58Z, last event 2026-08-16T01:38:57Z (exactly 10h), item
`Live/Collab/Ratu 25.01.01.mp4` at 35% with per-second progress. Throughput
was ~66 files/h; 1651 files needed ~25h. The job was launched as a
foreground wait with the max timeout, so the wrapper killed a healthy encode
on schedule. Default 120s-then-background does **not** kill; an explicit
timeout does.

Agents must submit any `--yes` encode that can exceed ~30 minutes with
`psammophis --systemd …` (a `Type=simple` user unit, `TimeoutStartSec=infinity`,
`KillMode=mixed`). Watch `systemctl --user status psammophis-transcode.service`
and `runs show`. Do not "observe" by holding a 10h tool call, and do not use
`systemd-run`'s default oneshot type — that start timeout is the same class
of fuse. See `AGENTS.md` (Progress and journals) and
`skills/psm-transcode/SKILL.md`.

## Lesson for future changes here

Any change that gives a previously-non-raising internal helper a new way to raise needs an audit of every call site that already wraps it in its own exception handling, not just the call sites that are the obvious "primary" trigger. The bug above was invisible from `_wait_after_cancel()`'s own diff -- it only existed in the interaction with code the diff didn't touch.
