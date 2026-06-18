# Walkthrough — Verifiable SAT Harness Patches (EXP-004-alpha)

This walkthrough documents the corrections made to the verification and solver components of `exp004_sat_challenge.py` to ensure complete safety, performance under Windows/WSL/CI environments, and correct verdict reporting.

## 🛠️ Changes Implemented

### 1. Robust and Safe Parallel Brute Force
- Removed the immediate `executor.shutdown(wait=False, cancel_futures=True)` call inside the `as_completed` loop to prevent dangling worker processes and potential resource leaks in constrained CI/sandbox environments.
- Allowed all chunks to finish cleanly without early `break` statements.
- Managed worker counts via the environment variable `EXP004_BF_WORKERS` (defaulting to 4, capping at 8).
- Implemented error tracking for workers: exceptions are no longer silently swallowed, and if all processes fail, the solver returns a distinct `VERIFIER_ERROR`.

### 2. Empty Clause CDCL Fix
- Added immediate empty clause verification in CDCL unit propagation (`_all_propagate`) and satisfaction checks (`all_sat` inside `_cdcl()`).
- This fixes the bug where CDCL could return a dummy SAT assignment for the degenerate `[[]]` empty clause instance, which now correctly resolves to `CORRECT_UNSAT_SMALL` instead of being flagged as an invalid assignment (`FOUND_INVALID_SAT`).

### 3. Environment/CI Compatibility
- Imported the `multiprocessing` module as `mp` and added `mp.freeze_support()` at the entry point to guarantee compatibility on Windows/CI environments when spawning child processes.

### 4. Safety Verdict Counts
- Modified `fail_count` (in `run_layer()`) and `fail_total` (in `run_experiment()`) to include both `VERIFIER_ERROR` and `VERIFIER_TIMEOUT` as failure states.

---

## 🧪 Validation Results

Both native Windows and WSL (Linux Genesis 6.6) environments were tested:

```text
========================================================================
  EXP-004 OVERALL RESULTS
========================================================================
    Layer 'L1: Toy sanity' done: 14 runs, 8 SAT, 6 UNSAT, 0 timeout, 0 fail
    Layer 'L2: Random 3-SAT n<=20' done: 30 runs, 24 SAT, 6 UNSAT, 0 timeout, 0 fail
    Layer 'L3: Phase transition n=20..100' done: 4 runs, 2 SAT, 2 UNSAT, 0 timeout, 0 fail
    Layer 'L4: Challenge benchmarks (PHP / SAT Competition-style)' done: 10 runs, 6 SAT, 4 UNSAT, 0 timeout, 0 fail

  Total runs: 58
  SAT found & verified: 40
  UNSAT (verified or claimed): 18
  Timeout (solver): 0
  Timeout (verifier): 0
  Failures (invalid/false/crash/verifier): 0
  Pass rate: 100.0%
```

All 58 test configurations pass successfully with exactly `100.0%` pass rate, and the brute-force parallel execution behaves reliably without generating dangling zombie processes.
