"""
exp004_sat_challenge.py — Verifiable SAT/3-SAT Solver Challenge.

Strict rules:
  - Every SAT assignment is verified clause-by-clause by an independent verifier.
  - UNSAT is only accepted for small n (brute-force proven) or with proof checker.
  - UNSAT claims on large instances → UNSAT_CLAIM_UNVERIFIED.
  - No hardcoded answers, no label leakage.
  - All metrics (time, memory, decisions, conflicts) recorded per run.

Target layers:
  L1: Toy sanity tests (manual SAT/UNSAT).
  L2: Random 3-SAT n=8..20, brute-force verified.
  L3: Phase transition region n=20..100.
  L4: Challenge benchmarks (SAT Competition / DIMACS).
"""

import math
import random
import time
import hashlib
import tracemalloc
import csv
import json
import os
import sys
import re
import concurrent.futures
import multiprocessing as mp
from pathlib import Path
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional

import numpy as np

# ── Paths ──────────────────────────────────────────────────────
FIGS = Path(__file__).resolve().parent / "exp_docs" / "figures"
INSTANCES = Path(__file__).resolve().parent / "exp_docs" / "instances"
RESULTS = Path(__file__).resolve().parent / "exp_docs"
FIGS.mkdir(parents=True, exist_ok=True)
INSTANCES.mkdir(parents=True, exist_ok=True)

# ── Verdict taxonomy ───────────────────────────────────────────
class Verdict(Enum):
    FOUND_VALID_SAT = "FOUND_VALID_SAT"
    FOUND_INVALID_SAT = "FOUND_INVALID_SAT"
    CORRECT_UNSAT_SMALL = "CORRECT_UNSAT_SMALL"
    UNSAT_CLAIM_UNVERIFIED = "UNSAT_CLAIM_UNVERIFIED"
    FALSE_UNSAT = "FALSE_UNSAT"
    FALSE_SAT = "FALSE_SAT"
    NO_SOLUTION_FOUND = "NO_SOLUTION_FOUND"
    TIMEOUT = "TIMEOUT"
    CRASH = "CRASH"
    PARSER_ERROR = "PARSER_ERROR"
    VERIFIER_ERROR = "VERIFIER_ERROR"
    VERIFIER_TIMEOUT = "VERIFIER_TIMEOUT"

# ── Data structures ────────────────────────────────────────────
@dataclass
class DIMACS:
    n_vars: int
    n_clauses: int
    clauses: list[list[int]]         # each clause is list of non-zero ints
    comment: str = ""
    source: str = "manual"
    sha256: str = ""

    def compute_hash(self):
        h = hashlib.sha256()
        h.update(str(self.n_vars).encode())
        h.update(b",")
        h.update(str(self.n_clauses).encode())
        for c in self.clauses:
            for lit in c:
                h.update(str(lit).encode())
                h.update(b" ")
            h.update(b"0\n")
        self.sha256 = h.hexdigest()
        return self.sha256


@dataclass
class SatAssignment:
    values: dict[int, bool]          # var -> True/False
    n_vars: int = 0

    def to_list(self) -> list[int]:
        return [1 if self.values.get(i, False) else -1 for i in range(1, self.n_vars + 1)]

    def to_dimacs_line(self) -> str:
        parts = [f"{i if self.values.get(i, False) else -i}" for i in range(1, self.n_vars + 1)]
        return " ".join(parts) + " 0"


@dataclass
class SolverMetrics:
    wall_time_ns: int = 0
    cpu_time_ns: int = 0
    peak_memory_kb: int = 0
    decisions: int = 0
    propagations: int = 0
    conflicts: int = 0
    backtracks: int = 0
    restarts: int = 0
    nodes_visited: int = 0


@dataclass
class SatResult:
    instance_id: str = ""
    sha256: str = ""
    n_vars: int = 0
    n_clauses: int = 0
    clause_var_ratio: float = 0.0
    source: str = "manual"
    seed: Optional[int] = None
    solver_name: str = "unknown"
    timeout_ms: int = 5000
    verdict: Verdict = Verdict.NO_SOLUTION_FOUND
    assignment: Optional[SatAssignment] = None
    metrics: SolverMetrics = field(default_factory=SolverMetrics)
    error: str = ""
    verification_wall_time_ns: int = 0     # brute force UNSAT confirmation time
    oracle_wall_time_ns: int = 0           # brute force oracle solver time


# ── Parser ─────────────────────────────────────────────────────
def parse_dimacs(text: str) -> DIMACS:
    clauses = []
    n_vars = 0
    n_clauses = 0
    comment = ""
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("c "):
            comment += line[2:] + "\n"
            continue
        if line.startswith("p cnf"):
            parts = line.split()
            if len(parts) >= 3:
                n_vars = int(parts[2])
            if len(parts) >= 4:
                n_clauses = int(parts[3])
            continue
        # clause line
        nums = list(map(int, line.split()))
        if not nums:
            continue
        if nums[-1] == 0:
            nums = nums[:-1]
        if nums:
            clauses.append(nums)
    return DIMACS(n_vars=n_vars, n_clauses=len(clauses),
                  clauses=clauses, comment=comment)


def dimacs_to_text(dimacs: DIMACS) -> str:
    lines = []
    if dimacs.comment:
        for c_line in dimacs.comment.strip().split("\n"):
            if c_line:
                lines.append(f"c {c_line}")
    lines.append(f"p cnf {dimacs.n_vars} {dimacs.n_clauses}")
    for clause in dimacs.clauses:
        lines.append(" ".join(map(str, clause)) + " 0")
    return "\n".join(lines)


def load_dimacs(path: str) -> DIMACS:
    with open(path, "r") as f:
        return parse_dimacs(f.read())


def save_dimacs(dimacs: DIMACS, path: str):
    dimacs.compute_hash()
    text = dimacs_to_text(dimacs)
    with open(path, "w") as f:
        f.write(text)
        f.write("\n")


# ── Verifier ───────────────────────────────────────────────────
def verify_assignment(dimacs: DIMACS, assignment: SatAssignment) -> tuple[bool, list[int]]:
    """Check assignment against all clauses. Returns (all_satisfied, [failing clause indices])."""
    failing = []
    for idx, clause in enumerate(dimacs.clauses):
        clause_sat = False
        for lit in clause:
            var = abs(lit)
            sign = lit > 0
            val = assignment.values.get(var, False)
            if val == sign:
                clause_sat = True
                break
        if not clause_sat:
            failing.append(idx)
    return len(failing) == 0, failing


# ── Generator ──────────────────────────────────────────────────
def generate_random_3sat(n_vars: int, n_clauses: int, seed: Optional[int] = None) -> DIMACS:
    rng = random.Random(seed)
    clauses = []
    seen = set()
    attempts = 0
    max_attempts = n_clauses * 10
    while len(clauses) < n_clauses and attempts < max_attempts:
        attempts += 1
        vars_in_clause = rng.sample(range(1, n_vars + 1), 3)
        clause = [v if rng.random() < 0.5 else -v for v in vars_in_clause]
        key = tuple(sorted(abs(v) for v in clause))
        if key not in seen:
            seen.add(key)
            clauses.append(clause)
    return DIMACS(n_vars=n_vars, n_clauses=len(clauses),
                  clauses=clauses, source=f"random_3sat_seed_{seed}")


# ── Brute force oracle ─────────────────────────────────────────
def _brute_force_chunk(dimacs: DIMACS, start_mask: int, end_mask: int, timeout_ns: int, start_wall: int) -> tuple[bool, Optional[SatAssignment], int]:
    """Search a chunk of configs. Returns (found, assignment, nodes_visited)."""
    n = dimacs.n_vars
    nodes = 0
    for mask in range(start_mask, end_mask):
        if (time.perf_counter_ns() - start_wall) >= timeout_ns:
            return False, None, nodes
        nodes += 1
        values = {}
        for var in range(1, n + 1):
            values[var] = bool((mask >> (var - 1)) & 1)
        assn = SatAssignment(values=values, n_vars=n)
        ok, _ = verify_assignment(dimacs, assn)
        if ok:
            return True, assn, nodes
    return False, None, nodes


def brute_force_oracle(dimacs: DIMACS, timeout_ms: int = 30000) -> SatResult:
    """Exhaustive search for SAT/UNSAT. Only for small n (<=20). Parallelized."""
    metrics = SolverMetrics()
    start_wall = time.perf_counter_ns()
    start_cpu = time.process_time_ns()
    timeout_ns = timeout_ms * 1_000_000
    tracemalloc.start()
    n = dimacs.n_vars
    found = False
    best_assignment = None
    total_configs = 1 << n

    if n <= 12:
        # For small n, run sequentially to avoid process spawn overhead
        for mask in range(total_configs):
            if (time.perf_counter_ns() - start_wall) >= timeout_ns:
                break
            metrics.nodes_visited += 1
            values = {}
            for var in range(1, n + 1):
                values[var] = bool((mask >> (var - 1)) & 1)
            assn = SatAssignment(values=values, n_vars=n)
            ok, _ = verify_assignment(dimacs, assn)
            if ok:
                found = True
                best_assignment = assn
                break
    else:
        max_env_workers = int(os.getenv("EXP004_BF_WORKERS", "4"))
        num_workers = min(os.cpu_count() or 1, max_env_workers, 8)

        chunk_size = (total_configs + num_workers - 1) // num_workers
        futures = []
        worker_errors = []

        with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as executor:
            for i in range(num_workers):
                start_mask = i * chunk_size
                end_mask = min(start_mask + chunk_size, total_configs)
                if start_mask < end_mask:
                    futures.append(
                        executor.submit(
                            _brute_force_chunk,
                            dimacs, start_mask, end_mask, timeout_ns, start_wall
                        )
                    )
            
            for fut in concurrent.futures.as_completed(futures):
                try:
                    ok, assn, visited = fut.result()
                    metrics.nodes_visited += visited
                    if ok and not found:
                        found = True
                        best_assignment = assn
                except Exception as e:
                    worker_errors.append(repr(e))

        if worker_errors and metrics.nodes_visited == 0 and not found:
            metrics.wall_time_ns = time.perf_counter_ns() - start_wall
            metrics.cpu_time_ns = time.process_time_ns() - start_cpu
            _, peak = tracemalloc.get_traced_memory()
            metrics.peak_memory_kb = peak // 1024
            tracemalloc.stop()

            return SatResult(
                verdict=Verdict.VERIFIER_ERROR,
                assignment=None,
                metrics=metrics,
                error="brute force worker failed: " + "; ".join(worker_errors[:3]),
            )

    metrics.wall_time_ns = time.perf_counter_ns() - start_wall
    metrics.cpu_time_ns = time.process_time_ns() - start_cpu
    _, peak = tracemalloc.get_traced_memory()
    metrics.peak_memory_kb = peak // 1024
    tracemalloc.stop()

    verdict = Verdict.FOUND_VALID_SAT if found else Verdict.CORRECT_UNSAT_SMALL
    if not found and metrics.wall_time_ns >= timeout_ns:
        verdict = Verdict.TIMEOUT
    result = SatResult(
        verdict=verdict,
        assignment=best_assignment,
        metrics=metrics,
    )
    return result


# ── DPLL baseline ──────────────────────────────────────────────
def solve_dpll(dimacs: DIMACS, timeout_ms: int = 5000) -> SatResult:
    """Minimal DPLL with unit propagation and pure literal elimination."""
    metrics = SolverMetrics()
    start_wall = time.perf_counter_ns()
    start_cpu = time.process_time_ns()
    timeout_ns = timeout_ms * 1_000_000
    tracemalloc.start()

    clauses = [list(c) for c in dimacs.clauses]   # mutable copy
    n_vars = dimacs.n_vars
    assignment = {}

    def _timeout_check():
        return (time.perf_counter_ns() - start_wall) >= timeout_ns

    def _unit_propagate(c: list[list[int]], a: dict) -> tuple[list[list[int]], dict, bool]:
        changed = True
        while changed:
            changed = False
            # Remove satisfied clauses, shorten falsified
            new_clauses = []
            for clause in c:
                clause_sat = False
                new_clause = []
                for lit in clause:
                    var = abs(lit)
                    if var in a:
                        if a[var] == (lit > 0):
                            clause_sat = True
                            break
                        else:
                            continue    # literal false, skip
                    else:
                        new_clause.append(lit)
                if clause_sat:
                    changed = True
                    continue
                if len(new_clause) == 0:
                    return [], a, True    # conflict
                if len(new_clause) == 1:
                    # unit clause
                    lit = new_clause[0]
                    var = abs(lit)
                    a[var] = (lit > 0)
                    metrics.propagations += 1
                    changed = True
                    continue
                new_clauses.append(new_clause)
            c = new_clauses
        return c, a, False

    def _pure_literal_elim(c: list[list[int]], a: dict):
        """Assign pure literals (appear only in one polarity)."""
        pos = set()
        neg = set()
        for clause in c:
            for lit in clause:
                var = abs(lit)
                if lit > 0:
                    pos.add(var)
                else:
                    neg.add(var)
        pure_vars = (pos - neg) | (neg - pos)
        for var in pure_vars:
            a[var] = (var in pos)    # True if positive-only, False if negative-only
            metrics.propagations += 1

    def _pick_var(c: list[list[int]], a: dict) -> Optional[int]:
        unassigned = set()
        for clause in c:
            for lit in clause:
                var = abs(lit)
                if var not in a:
                    unassigned.add(var)
        if not unassigned:
            return None
        # Choose variable appearing in most clauses (MW heuristic)
        best_var = max(unassigned, key=lambda v: sum(1 for cl in c if any(abs(l) == v for l in cl)))
        return best_var

    def _dpll(c: list[list[int]], a: dict) -> tuple[bool, dict]:
        metrics.nodes_visited += 1
        if _timeout_check():
            return False, a
        # Unit propagate
        c, a, conflict = _unit_propagate(c, a)
        if conflict:
            return False, a
        # Pure literal
        _pure_literal_elim(c, a)
        # Re-check after pure literal
        c, a, conflict = _unit_propagate(c, a)
        if conflict:
            return False, a
        # Check if all clauses satisfied
        if not c:
            return True, a
        # Pick variable
        var = _pick_var(c, a)
        if var is None:
            # All assigned but clauses remain? shouldn't happen
            return True, a
        # Branch
        for val in [True, False]:
            metrics.decisions += 1
            new_a = dict(a)
            new_a[var] = val
            ok, res_a = _dpll(c, new_a)
            if ok:
                return True, res_a
            metrics.backtracks += 1
        return False, a

    found, final_assignment = _dpll(clauses, assignment)

    metrics.wall_time_ns = time.perf_counter_ns() - start_wall
    metrics.cpu_time_ns = time.process_time_ns() - start_cpu
    _, peak = tracemalloc.get_traced_memory()
    metrics.peak_memory_kb = peak // 1024
    tracemalloc.stop()

    timed_out = _timeout_check()

    result = SatResult(
        n_vars=n_vars,
        n_clauses=len(dimacs.clauses),
        solver_name="dpll",
        timeout_ms=timeout_ms,
        verdict=Verdict.TIMEOUT if timed_out else (
            Verdict.FOUND_VALID_SAT if found else Verdict.UNSAT_CLAIM_UNVERIFIED
        ),
        assignment=SatAssignment(values=final_assignment, n_vars=n_vars) if found else None,
        metrics=metrics,
    )
    return result


# ── CDCL experimental solver ───────────────────────────────────
def solve_cdcl(dimacs: DIMACS, timeout_ms: int = 5000) -> SatResult:
    """CDCL solver with clause learning, VSIDS, and restarts.

    Uses a propagation queue (not 2WL) for reliability.
    """
    metrics = SolverMetrics()
    start_wall = time.perf_counter_ns()
    start_cpu = time.process_time_ns()
    timeout_ns = timeout_ms * 1_000_000
    tracemalloc.start()

    n_vars = dimacs.n_vars
    clause_db = [list(c) for c in dimacs.clauses]

    assignment = [0] * (n_vars + 1)       # 0=un, 1=True, -1=False
    trail = []                             # (var, reason_idx, dl)
    trail_lim = [0]
    dl = 0
    prop_queue = []                        # queue of literals to propagate

    activity = [0.0] * (n_vars + 1)
    var_inc = 1.0
    var_decay = 0.95
    restart_limit = 100
    learnt_indices = set()

    def _timeout():
        return (time.perf_counter_ns() - start_wall) >= timeout_ns

    def _is_true(lit: int) -> bool:
        v = assignment[abs(lit)]
        return v != 0 and (v == 1) == (lit > 0)

    def _is_false(lit: int) -> bool:
        v = assignment[abs(lit)]
        return v != 0 and (v == 1) != (lit > 0)

    def _enqueue(lit: int, reason: int):
        var = abs(lit)
        val = 1 if lit > 0 else -1
        if assignment[var] != 0:
            return
        assignment[var] = val
        trail.append((var, reason, dl))
        prop_queue.append(lit)
        metrics.propagations += 1

    def _all_propagate() -> Optional[int]:
        """Propagate all enqueued literals. Returns conflict clause or None."""
        # If there's an empty clause, it's an immediate conflict
        for ci, cl in enumerate(clause_db):
            if len(cl) == 0:
                return ci

        while prop_queue:
            if _timeout():
                return None
            lit = prop_queue.pop(0)
            var = abs(lit)
            # Current assignment of var determines which literals are false
            var_val = assignment[var]
            false_lit = var if var_val == -1 else -var  # the literal that is now false
            true_lit = -var if var_val == -1 else var    # the literal that is now true

            # Scan all clauses that are NOT satisfied and need checking
            for ci, cl in enumerate(clause_db):
                if not cl:
                    continue
                # Quick check: if clause is already satisfied, skip
                # We check by looking at the current false literal
                if false_lit not in cl and true_lit not in cl:
                    continue   # clause unchanged by this assignment
                if true_lit in cl:
                    continue   # clause satisfied by another literal

                # Clause not satisfied, may have been affected
                unset = None
                n_unset = 0
                n_false = 0
                for l in cl:
                    if _is_true(l):
                        break  # clause satisfied
                    if _is_false(l):
                        n_false += 1
                    else:
                        unset = l
                        n_unset += 1
                else:
                    # No literal was true
                    if n_false == len(cl):
                        return ci   # conflict
                    if n_unset == 1 and unset is not None:
                        _enqueue(unset, ci)
        return None

    def _analyze(conflict_idx: int) -> tuple[list[int], int]:
        """1-UIP conflict clause learning via reverse-trail resolution.

        Walks the implication trail backwards from the conflict,
        resolving away the most-recently-assigned literal at the
        current decision level until exactly one remains.
        Returns the learned clause and its backtrack level.
        """
        # Fast var -> level lookup
        var_lvl = {}
        for v, _r, l in trail:
            var_lvl[v] = l

        learnt_set = set()
        for lit in clause_db[conflict_idx]:
            learnt_set.add(lit)

        seen = [False] * (n_vars + 1)
        for lit in learnt_set:
            seen[abs(lit)] = True

        n_at_current = sum(
            1 for lit in learnt_set if var_lvl.get(abs(lit), -1) == dl
        )

        # Walk trail backwards, resolving away literals at current dl
        for v, reason, l in reversed(trail):
            if n_at_current <= 1:
                break
            if l != dl or not seen[v]:
                continue
            # Resolve away variable v at current dl:
            #  1) remove all literals mentioning v from learnt_set
            #  2) add all literals from the reason clause (except v)
            seen[v] = False
            n_at_current -= sum(1 for lit in learnt_set if abs(lit) == v)
            learnt_set = {lit for lit in learnt_set if abs(lit) != v}
            if reason != -1:  # not a decision literal
                for lit in clause_db[reason]:
                    rvar = abs(lit)
                    if rvar == v:
                        continue  # skip pivot variable
                    if not seen[rvar]:
                        seen[rvar] = True
                        learnt_set.add(lit)
                        if var_lvl.get(rvar, -1) == dl:
                            n_at_current += 1

        # Compute bt_level: max level < dl among learned clause
        bt_level = 0
        for lit in learnt_set:
            lvl = var_lvl.get(abs(lit), 0)
            if lvl < dl and lvl > bt_level:
                bt_level = lvl

        learnt_list = list(learnt_set)
        for lit in learnt_list:
            activity[abs(lit)] += var_inc

        return learnt_list, bt_level

    def _backtrack(to_level: int):
        nonlocal dl
        metrics.backtracks += 1
        while len(trail_lim) > to_level + 1:
            trail_lim.pop()
        # Unassign all at levels > to_level
        trail_copy = list(trail)
        trail.clear()
        for var, reason, lvl in trail_copy:
            if lvl <= to_level:
                trail.append((var, reason, lvl))
            else:
                assignment[var] = 0
        dl = to_level

    def _pick_branch_var() -> Optional[int]:
        metrics.decisions += 1
        metrics.nodes_visited += 1
        best_var = 0
        best_act = -1.0
        for var in range(1, n_vars + 1):
            if assignment[var] == 0 and activity[var] > best_act:
                best_act = activity[var]
                best_var = var
        if best_var == 0:
            for var in range(1, n_vars + 1):
                if assignment[var] == 0:
                    return var
            return None
        return best_var

    def _cdcl() -> list[int]:
        nonlocal dl, var_inc, restart_limit
        while True:
            if _timeout():
                return []
            conflict = _all_propagate()
            while conflict is not None:
                metrics.conflicts += 1
                if dl == 0:
                    return []
                learnt, bt_level = _analyze(conflict)
                if not learnt:
                    return []
                learnt_indices.add(len(clause_db))
                clause_db.append(learnt)
                var_inc *= (1.0 / var_decay)
                _backtrack(bt_level)
                prop_queue.clear()
                # Count unassigned after backtrack — enqueue only if unit
                unassigned = [l for l in learnt
                              if assignment[abs(l)] == 0]
                if len(unassigned) == 1:
                    _enqueue(unassigned[0], len(clause_db) - 1)
                conflict = _all_propagate()

            if metrics.conflicts >= restart_limit:
                _backtrack(0)
                dl = 0
                prop_queue.clear()
                metrics.restarts += 1
                restart_limit = int(restart_limit * 1.5)
                continue

            # Check if all clauses satisfied
            all_sat = True
            for cl in clause_db:
                if len(cl) == 0:
                    all_sat = False
                    break
                if not any(_is_true(l) for l in cl):
                    all_sat = False
                    break
            if all_sat:
                return [assignment[v] for v in range(1, n_vars + 1)]

            var = _pick_branch_var()
            if var is None:
                return [assignment[v] for v in range(1, n_vars + 1)]

            dl += 1
            trail_lim.append(len(trail))
            _enqueue(var, -1)

    final_assign = _cdcl()
    metrics.wall_time_ns = time.perf_counter_ns() - start_wall
    metrics.cpu_time_ns = time.process_time_ns() - start_cpu
    _, peak = tracemalloc.get_traced_memory()
    metrics.peak_memory_kb = peak // 1024
    tracemalloc.stop()

    timed_out = (time.perf_counter_ns() - start_wall) >= timeout_ns
    found = bool(final_assign)
    assn_values = {}
    if found:
        for var in range(1, n_vars + 1):
            assn_values[var] = (final_assign[var - 1] == 1)
    # Return assignment regardless of validity — let run_instance's
    # external verifier catch invalid SAT as FOUND_INVALID_SAT.
    # Do NOT mask invalid SAT as UNSAT_CLAIM_UNVERIFIED.
    return SatResult(
        n_vars=n_vars, n_clauses=len(dimacs.clauses),
        solver_name="cdcl", timeout_ms=timeout_ms,
        verdict=Verdict.TIMEOUT if timed_out else (
            Verdict.FOUND_VALID_SAT if found else Verdict.UNSAT_CLAIM_UNVERIFIED),
        assignment=SatAssignment(values=assn_values, n_vars=n_vars) if found else None,
        metrics=metrics,
    )


# ═══════════════════════════════════════════════════════════════
#  Test cases
# ═══════════════════════════════════════════════════════════════

def make_toy_cases():
    """Layer 1: toy sanity — SAT and UNSAT."""
    return [
        DIMACS(1, 1, [[1]], comment="SAT: x1", source="toy_sat_1var"),
        DIMACS(1, 2, [[1], [-1]], comment="UNSAT: x1 AND not x1", source="toy_unsat_1var_conflict"),
        DIMACS(2, 2, [[1, 2], [-1, 2]], comment="SAT: (x1 OR x2) AND (not x1 OR x2)", source="toy_sat_2var_simple"),
        DIMACS(2, 4, [[1, 2], [1, -2], [-1, 2], [-1, -2]],
               comment="UNSAT: all 4 clauses over x1,x2", source="toy_unsat_2var_all"),
        # SAT: (x1 OR not x2) AND (not x1 OR x2)
        DIMACS(2, 2, [[1, -2], [-1, 2]],
               comment="SAT: (x1 OR not x2) AND (not x1 OR x2)", source="toy_sat_2var_equiv"),
        # UNSAT: single variable tautology? no — unit conflict:
        DIMACS(1, 2, [[1], [1]], comment="SAT: x1 AND x1 is just x1", source="toy_sat_1var_tautology"),
        # Degenerate: empty clause
        DIMACS(1, 1, [[]], comment="UNSAT: empty clause", source="toy_unsat_empty"),
    ]


def _save_instance(dimacs: DIMACS, prefix: str = ""):
    """Save a generated DIMACS instance to the instances directory."""
    fname = f"{prefix or dimacs.source}.cnf"
    path = str(INSTANCES / fname)
    save_dimacs(dimacs, path)
    return path


def generate_layer2_cases(seeds=None):
    """Layer 2: random 3-SAT n=8..16, m/n ≈ 4.26. (n=18,20 skipped for speed.)"""
    if seeds is None:
        seeds = [42]
    cases = []
    for n in [8, 10, 12, 14, 16]:
        m = int(n * 4.26)
        for seed in seeds:
            dimacs = generate_random_3sat(n, m, seed=seed)
            dimacs.comment = f"Random 3-SAT n={n} m={m} seed={seed}"
            dimacs.source = f"layer2_n{n}_seed{seed}"
            dimacs.compute_hash()
            _save_instance(dimacs)
            cases.append(dimacs)
    return cases


def generate_layer3_cases(seeds=None):
    """Layer 3: phase transition region n=20, 50."""
    if seeds is None:
        seeds = [42]
    configs = [
        (20, 91),
        (50, 218),
    ]
    cases = []
    for n, m in configs:
        for seed in seeds:
            dimacs = generate_random_3sat(n, m, seed=seed)
            dimacs.comment = f"Phase transition 3-SAT n={n} m={m} seed={seed}"
            dimacs.source = f"layer3_n{n}_seed{seed}"
            dimacs.compute_hash()
            _save_instance(dimacs)
            cases.append(dimacs)
    return cases


# ── Permutation invariance test ────────────────────────────────
def test_permutation_invariance(dimacs: DIMACS, solver_fn, timeout_ms: int = 5000):
    """Check that solver verdict is invariant under clause shuffling and var renaming.

    Returns dict with original/shuffled/renamed verdicts, or None on mismatch.
    """
    base_name = dimacs.source or "unknown"
    rng = random.Random(42)
    # 1) Shuffle clauses
    shuffled = DIMACS(
        n_vars=dimacs.n_vars, n_clauses=dimacs.n_clauses,
        clauses=[list(c) for c in dimacs.clauses],
        source=dimacs.source, comment="shuffled: " + dimacs.comment,
    )
    rng.shuffle(shuffled.clauses)
    shuffled.compute_hash()

    # 2) Rename variables
    vars_perm = list(range(1, dimacs.n_vars + 1))
    rng.shuffle(vars_perm)
    inv_perm = [0] * (dimacs.n_vars + 1)
    for new_v, old_v in enumerate(vars_perm, start=1):
        inv_perm[old_v] = new_v
    renamed_clauses = []
    for cl in dimacs.clauses:
        new_cl = []
        for lit in cl:
            old_var = abs(lit)
            new_var = inv_perm[old_var]
            new_lit = new_var if lit > 0 else -new_var
            new_cl.append(new_lit)
        renamed_clauses.append(new_cl)
    renamed = DIMACS(
        n_vars=dimacs.n_vars, n_clauses=dimacs.n_clauses,
        clauses=renamed_clauses,
        source=dimacs.source, comment="renamed: " + dimacs.comment,
    )
    renamed.compute_hash()

    orig_result = solver_fn(dimacs, timeout_ms=timeout_ms)
    shuffle_result = solver_fn(shuffled, timeout_ms=timeout_ms)
    rename_result = solver_fn(renamed, timeout_ms=timeout_ms)

    orig_v = orig_result.verdict
    shuffle_v = shuffle_result.verdict
    rename_v = rename_result.verdict

    ok = (
        (orig_v == shuffle_v == rename_v) or
        # TIMEOUT is acceptable on any variant
        set([orig_v, shuffle_v, rename_v]).issubset(
            {Verdict.TIMEOUT, orig_v})
    )
    return {
        "instance": base_name,
        "original": orig_v.value,
        "shuffled": shuffle_v.value,
        "renamed": rename_v.value,
        "invariant": ok,
    }


# ── External challenge benchmarks ──────────────────────────────
def _pigeonhole(n_holes: int) -> DIMACS:
    """PHP: n_holes+1 pigeons into n_holes holes — always UNSAT."""
    n_pigeons = n_holes + 1
    n_vars = n_pigeons * n_holes
    clauses = []
    # Each pigeon in at least one hole
    for p in range(n_pigeons):
        clause = [p * n_holes + h + 1 for h in range(n_holes)]
        clauses.append(clause)
    # No hole gets two pigeons
    for h in range(n_holes):
        for p1 in range(n_pigeons):
            for p2 in range(p1 + 1, n_pigeons):
                clauses.append([
                    -(p1 * n_holes + h + 1),
                    -(p2 * n_holes + h + 1),
                ])
    return DIMACS(
        n_vars=n_vars, n_clauses=len(clauses),
        clauses=clauses,
        source=f"challenge_php{n_holes}",
        comment=f"PHP {n_pigeons} pigeons -> {n_holes} holes (UNSAT)",
    )


def _small_sat_competition_style(seed: int, n: int, m: int, label: str, sat: bool = True) -> DIMACS:
    """Generate a random 3-SAT instance in the style of SAT Competition benchmarks."""
    dimacs = generate_random_3sat(n, m, seed=seed)
    dimacs.source = f"challenge_{label}"
    polarity = "sat" if sat else "unsat"
    dimacs.comment = f"SAT Competition-style 3-SAT n={n} m={m} seed={seed} ({polarity})"
    dimacs.compute_hash()
    return dimacs


def generate_layer4_cases():
    """Layer 4: external-style challenge benchmarks (no external file dependency)."""
    cases = []

    # 1) PHP 4 holes → 5 pigeons → UNSAT (n=20 vars, 45 clauses)
    php = _pigeonhole(4)
    php.compute_hash()
    _save_instance(php, "L4_php4")
    cases.append(php)

    # 2) Random 3-SAT n=20, m=91 → phase transition, SAT with seed 7
    sat20 = _small_sat_competition_style(7, 20, 91, "n20_seed7", sat=True)
    _save_instance(sat20, "L4_n20_sat_seed7")
    cases.append(sat20)

    # 3) Random 3-SAT n=24, m=102 → hardness peak region, SAT with seed 13
    sat24 = _small_sat_competition_style(13, 24, 102, "n24_seed13", sat=True)
    _save_instance(sat24, "L4_n24_sat_seed13")
    cases.append(sat24)

    # 4) Random 3-SAT n=30, m=128 → SAT with seed 99
    sat30 = _small_sat_competition_style(99, 30, 128, "n30_seed99", sat=True)
    _save_instance(sat30, "L4_n30_sat_seed99")
    cases.append(sat30)

    # 5) PHP 6 holes → 7 pigeons → UNSAT (n=42 vars)
    php6 = _pigeonhole(6)
    php6.compute_hash()
    _save_instance(php6, "L4_php6")
    cases.append(php6)

    return cases


# ═══════════════════════════════════════════════════════════════
#  Experiment runner
# ═══════════════════════════════════════════════════════════════

def run_instance(dimacs: DIMACS, solver_name: str, instance_id: str,
                 timeout_ms: int, seed: Optional[int] = None) -> SatResult:
    """Run a single instance with the given solver."""
    result = SatResult(
        instance_id=instance_id,
        sha256=dimacs.sha256,
        n_vars=dimacs.n_vars,
        n_clauses=dimacs.n_clauses,
        clause_var_ratio=dimacs.n_clauses / max(1, dimacs.n_vars),
        source=dimacs.source,
        seed=seed,
        solver_name=solver_name,
        timeout_ms=timeout_ms,
    )
    try:
        bf_timeout = max(timeout_ms, 60000)
        if solver_name == "brute_force":
            res = brute_force_oracle(dimacs, timeout_ms=bf_timeout)
            result.oracle_wall_time_ns = res.metrics.wall_time_ns if res.metrics else 0
        elif solver_name == "dpll":
            res = solve_dpll(dimacs, timeout_ms=timeout_ms)
        elif solver_name == "cdcl":
            res = solve_cdcl(dimacs, timeout_ms=timeout_ms)
        else:
            result.verdict = Verdict.CRASH
            result.error = f"unknown solver: {solver_name}"
            return result

        result.verdict = res.verdict
        result.assignment = res.assignment
        result.metrics = res.metrics
        result.error = res.error

        # Verify SAT assignments
        if result.verdict == Verdict.FOUND_VALID_SAT and result.assignment:
            ok, failing = verify_assignment(dimacs, result.assignment)
            if not ok:
                result.verdict = Verdict.FOUND_INVALID_SAT
                result.error = f"assignment fails on {len(failing)} clauses"
        elif result.verdict == Verdict.UNSAT_CLAIM_UNVERIFIED:
            # Check brute force for small n — track verification time separately
            if dimacs.n_vars <= 20:
                v_start = time.perf_counter_ns()
                bf = brute_force_oracle(dimacs, timeout_ms=bf_timeout)
                result.verification_wall_time_ns = time.perf_counter_ns() - v_start
                if bf.verdict == Verdict.FOUND_VALID_SAT:
                    result.verdict = Verdict.FALSE_UNSAT
                    result.error = "solver said UNSAT but brute force found assignment"
                elif bf.verdict == Verdict.TIMEOUT:
                    result.verdict = Verdict.VERIFIER_TIMEOUT
                    result.error = "brute force verification timed out"
                elif bf.verdict == Verdict.VERIFIER_ERROR:
                    result.verdict = Verdict.VERIFIER_ERROR
                    result.error = bf.error
                else:
                    result.verdict = Verdict.CORRECT_UNSAT_SMALL
    except Exception as e:
        result.verdict = Verdict.CRASH
        result.error = str(e)

    return result


def run_layer(name: str, instances: list[DIMACS],
              solvers: list[str], timeout_ms: int,
              bf_small: bool = True) -> tuple[list[SatResult], str]:
    """Run all solvers on all instances in a layer."""
    print(f"\n{'=' * 72}")
    print(f"  LAYER: {name}")
    print(f"  Instances: {len(instances)}, Solvers: {solvers}")
    print(f"{'=' * 72}")

    all_results = []
    for i, dimacs in enumerate(instances):
        source_name = dimacs.source if (dimacs.source and str(dimacs.source).strip()) else f"instance_{i}"
        for solver in solvers:
            # Skip brute force for large instances
            if solver == "brute_force" and dimacs.n_vars > 20:
                continue
            instance_id = f"{source_name}_{i}_{solver}"
            result = run_instance(dimacs, solver, instance_id,
                                  timeout_ms=timeout_ms)
            all_results.append(result)
            verdict_str = result.verdict.value
            t_ms = result.metrics.wall_time_ns / 1_000_000 if result.metrics else 0
            print(f"  [{result.solver_name:<12}] n={result.n_vars:<4} "
                  f"c={result.n_clauses:<5} "
                  f"verdict={verdict_str:<30} t={t_ms:.1f}ms")
            if result.error:
                print(f"         error: {result.error}")

    # Summary
    sat_count = sum(1 for r in all_results if r.verdict == Verdict.FOUND_VALID_SAT)
    unsat_count = sum(1 for r in all_results if r.verdict in (
        Verdict.CORRECT_UNSAT_SMALL, Verdict.UNSAT_CLAIM_UNVERIFIED))
    fail_count = sum(1 for r in all_results if r.verdict in (
        Verdict.FOUND_INVALID_SAT, Verdict.FALSE_SAT, Verdict.FALSE_UNSAT,
        Verdict.CRASH, Verdict.VERIFIER_ERROR, Verdict.VERIFIER_TIMEOUT))
    timeout_count = sum(1 for r in all_results if r.verdict == Verdict.TIMEOUT)

    summary = (f"  Layer '{name}' done: {len(all_results)} runs, "
               f"{sat_count} SAT, {unsat_count} UNSAT, "
               f"{timeout_count} timeout, {fail_count} fail")
    print(f"  {summary}")
    return all_results, summary


def run_experiment():
    """Orchestrate all 4 layers."""
    print("=" * 72)
    print("  EXP-004: SAT/3-SAT VERIFIABLE SOLVER CHALLENGE")
    print("  Strict verification: every SAT assignment checked clause-by-clause.")
    print("=" * 72)

    all_results = []
    summaries = []
    timeout_l1 = 2000
    timeout_l2 = 10000
    timeout_l3 = 15000

    # L1: Toy
    toy_cases = make_toy_cases()
    res, s = run_layer("L1: Toy sanity", toy_cases,
                       ["dpll", "cdcl"], timeout_ms=timeout_l1)
    all_results.extend(res)
    summaries.append(s)

    # Verify toy cases with brute force
    print("\n  -- L1 brute force verification --")
    for dimacs in toy_cases:
        bf = brute_force_oracle(dimacs)
        verdict = bf.verdict.value
        print(f"    bf: n={dimacs.n_vars} c={dimacs.n_clauses} -> {verdict}")

    # L2: Random n=8..20
    l2_cases = generate_layer2_cases(seeds=[42, 123])
    res, s = run_layer("L2: Random 3-SAT n<=20", l2_cases,
                       ["brute_force", "dpll", "cdcl"], timeout_ms=timeout_l2)
    all_results.extend(res)
    summaries.append(s)

    # L3: Phase transition
    l3_cases = generate_layer3_cases(seeds=[42])
    res, s = run_layer("L3: Phase transition n=20..100", l3_cases,
                       ["dpll", "cdcl"], timeout_ms=timeout_l3)
    all_results.extend(res)
    summaries.append(s)

    # L4: External-style challenge benchmarks
    l4_cases = generate_layer4_cases()
    res, s = run_layer("L4: Challenge benchmarks (PHP / SAT Competition-style)",
                       l4_cases, ["dpll", "cdcl"], timeout_ms=15000)
    all_results.extend(res)
    summaries.append(s)

    # ── Permutation invariance test (on first L2 instance) ──
    print(f"\n{'=' * 72}")
    print("  PERMUTATION INVARIANCE TEST")
    print(f"{'=' * 72}")
    if l2_cases:
        test_case = l2_cases[0]
        for solver_name, solver_fn in [("dpll", solve_dpll), ("cdcl", solve_cdcl)]:
            result = test_permutation_invariance(test_case, solver_fn, timeout_ms=5000)
            status = "PASS" if result["invariant"] else "FAIL"
            print(f"  [{solver_name}] {result['instance']}: "
                  f"orig={result['original']}, "
                  f"shuffled={result['shuffled']}, "
                  f"renamed={result['renamed']} -> [{status}]")

    # ── Global summary ──
    print(f"\n{'=' * 72}")
    print("  EXP-004 OVERALL RESULTS")
    print(f"{'=' * 72}")
    for s in summaries:
        print(f"  {s}")

    sat_total = sum(1 for r in all_results if r.verdict == Verdict.FOUND_VALID_SAT)
    unsat_total = sum(1 for r in all_results if r.verdict in (
        Verdict.CORRECT_UNSAT_SMALL, Verdict.UNSAT_CLAIM_UNVERIFIED))
    fail_total = sum(1 for r in all_results if r.verdict in (
        Verdict.FOUND_INVALID_SAT, Verdict.FALSE_SAT, Verdict.FALSE_UNSAT,
        Verdict.CRASH, Verdict.VERIFIER_ERROR, Verdict.VERIFIER_TIMEOUT))
    timeout_total = sum(1 for r in all_results if r.verdict == Verdict.TIMEOUT)
    verifier_timeout_total = sum(1 for r in all_results if r.verdict == Verdict.VERIFIER_TIMEOUT)
    total = len(all_results)

    print(f"\n  Total runs: {total}")
    print(f"  SAT found & verified: {sat_total}")
    print(f"  UNSAT (verified or claimed): {unsat_total}")
    print(f"  Timeout (solver): {timeout_total}")
    print(f"  Timeout (verifier): {verifier_timeout_total}")
    print(f"  Failures (invalid/false/crash/verifier): {fail_total}")
    print(f"  Pass rate: {100.0 * (total - fail_total) / max(1, total):.1f}%")

    # Generate plots
    print("\n-- Generating plots --")
    plot_time_vs_n(all_results)
    plot_success_by_layer(all_results)
    plot_nodes_vs_time(all_results)
    plot_verdict_matrix(all_results)
    print("  Plots done.")

    # Save results
    save_results(all_results)

    return all_results


def save_results(results: list[SatResult]):
    csv_path = RESULTS / "results_exp004.csv"
    jsonl_path = RESULTS / "results_exp004.jsonl"
    fields = ["instance_id", "sha256", "n_vars", "n_clauses", "clause_var_ratio",
              "source", "seed", "solver_name", "timeout_ms", "verdict",
              "wall_time_ns", "cpu_time_ns", "peak_memory_kb",
              "decisions", "propagations", "conflicts", "backtracks",
              "restarts", "nodes_visited", "error",
              "verification_wall_time_ns", "oracle_wall_time_ns"]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            row = {
                "instance_id": r.instance_id,
                "sha256": r.sha256,
                "n_vars": r.n_vars,
                "n_clauses": r.n_clauses,
                "clause_var_ratio": f"{r.clause_var_ratio:.4f}",
                "source": r.source,
                "seed": r.seed,
                "solver_name": r.solver_name,
                "timeout_ms": r.timeout_ms,
                "verdict": r.verdict.value,
                "wall_time_ns": r.metrics.wall_time_ns if r.metrics else 0,
                "cpu_time_ns": r.metrics.cpu_time_ns if r.metrics else 0,
                "peak_memory_kb": r.metrics.peak_memory_kb if r.metrics else 0,
                "decisions": r.metrics.decisions if r.metrics else 0,
                "propagations": r.metrics.propagations if r.metrics else 0,
                "conflicts": r.metrics.conflicts if r.metrics else 0,
                "backtracks": r.metrics.backtracks if r.metrics else 0,
                "restarts": r.metrics.restarts if r.metrics else 0,
                "nodes_visited": r.metrics.nodes_visited if r.metrics else 0,
                "error": r.error,
                "verification_wall_time_ns": r.verification_wall_time_ns,
                "oracle_wall_time_ns": r.oracle_wall_time_ns,
            }
            w.writerow(row)
    with open(jsonl_path, "w") as f:
        for r in results:
            d = asdict(r)
            d["verdict"] = r.verdict.value
            f.write(json.dumps(d, default=str) + "\n")
    print(f"  Results saved: {csv_path}, {jsonl_path} ({len(results)} rows)")


# ═══════════════════════════════════════════════════════════════
#  Plots
# ═══════════════════════════════════════════════════════════════

def plot_time_vs_n(results: list[SatResult]):
    """Wall time vs n for successful SAT instances."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    for solver in ["dpll", "cdcl"]:
        xs, ys = [], []
        for r in results:
            if r.solver_name == solver and r.metrics and r.metrics.wall_time_ns > 0:
                xs.append(r.n_vars)
                ys.append(r.metrics.wall_time_ns / 1_000_000)
        if xs:
            ax.scatter(xs, ys, label=solver, alpha=0.7, s=30)
    # Add brute force
    xs_bf, ys_bf = [], []
    for r in results:
        if r.solver_name == "brute_force" and r.metrics and r.metrics.wall_time_ns > 0:
            xs_bf.append(r.n_vars)
            ys_bf.append(r.metrics.wall_time_ns / 1_000_000)
    if xs_bf:
        ax.scatter(xs_bf, ys_bf, label="brute_force", alpha=0.7, s=30,
                   marker="x", color="black")

    ax.set_xlabel("Number of variables (n)")
    ax.set_ylabel("Wall time (ms)")
    ax.set_title("EXP-004: Solver time vs problem size", fontsize=12)
    ax.set_yscale("log")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = FIGS / "exp004_time_vs_n.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [1/4] {path.name}")


def plot_success_by_layer(results: list[SatResult]):
    """Stacked bar: SAT / UNSAT / TIMEOUT / FAIL per layer."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    layers = ["L1", "L2", "L3", "L4"]
    layer_map = {"L1": [], "L2": [], "L3": [], "L4": []}
    for r in results:
        src = r.source or ""
        if src.startswith("toy") or "toy" in r.instance_id:
            layer_map["L1"].append(r)
        elif "layer2" in src or "layer2" in r.instance_id:
            layer_map["L2"].append(r)
        elif "layer3" in src or "layer3" in r.instance_id:
            layer_map["L3"].append(r)
        else:
            layer_map["L4"].append(r)

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(layers))
    width = 0.6
    colors = {"SAT": "#2ecc71", "UNSAT": "#3498db",
              "TIMEOUT": "#f39c12", "FAIL": "#e74c3c"}

    for i, layer in enumerate(layers):
        rs = layer_map[layer]
        if not rs:
            continue
        sat = sum(1 for r in rs if r.verdict == Verdict.FOUND_VALID_SAT)
        unsat = sum(1 for r in rs if r.verdict in (
            Verdict.CORRECT_UNSAT_SMALL, Verdict.UNSAT_CLAIM_UNVERIFIED))
        timeout = sum(1 for r in rs if r.verdict == Verdict.TIMEOUT)
        fail = sum(1 for r in rs if r.verdict not in (
            Verdict.FOUND_VALID_SAT, Verdict.CORRECT_UNSAT_SMALL,
            Verdict.UNSAT_CLAIM_UNVERIFIED, Verdict.TIMEOUT))

        bottom = 0
        for label, val in [("SAT", sat), ("UNSAT", unsat),
                           ("TIMEOUT", timeout), ("FAIL", fail)]:
            if val > 0:
                ax.bar(i, val, width, bottom=bottom, color=colors[label],
                       label=label if i == 0 else "", alpha=0.85)
                ax.text(i, bottom + val / 2, str(val), ha="center", va="center",
                        fontweight="bold", fontsize=11, color="white")
                bottom += val

    ax.set_xticks(x)
    ax.set_xticklabels(layers)
    ax.set_title("EXP-004: Results by layer", fontsize=12)
    ax.set_ylabel("Number of runs")
    ax.legend()
    ax.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    path = FIGS / "exp004_success_by_layer.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [2/4] {path.name}")


def plot_nodes_vs_time(results: list[SatResult]):
    """Nodes visited vs wall time."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    for solver in ["dpll", "cdcl", "brute_force"]:
        xs, ys = [], []
        for r in results:
            if r.solver_name == solver and r.metrics and r.metrics.nodes_visited > 0:
                xs.append(r.metrics.nodes_visited)
                ys.append(r.metrics.wall_time_ns / 1_000_000)
        if xs:
            marker = "x" if solver == "brute_force" else "o"
            color = "black" if solver == "brute_force" else None
            ax.scatter(xs, ys, label=solver, alpha=0.7, s=20,
                       marker=marker, color=color)

    ax.set_xlabel("Nodes visited")
    ax.set_ylabel("Wall time (ms)")
    ax.set_title("EXP-004: Nodes vs wall time", fontsize=12)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    path = FIGS / "exp004_nodes_vs_time.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [3/4] {path.name}")


def plot_verdict_matrix(results: list[SatResult]):
    """Heatmap: solver x instance -> verdict."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    solvers = sorted(set(r.solver_name for r in results))
    # Collect unique instances
    instance_ids = []
    seen = set()
    for r in results:
        base_id = r.instance_id.replace(f"_{r.solver_name}", "")
        if base_id not in seen:
            seen.add(base_id)
            instance_ids.append(base_id)

    verdict_order = [Verdict.FOUND_VALID_SAT, Verdict.CORRECT_UNSAT_SMALL,
                     Verdict.UNSAT_CLAIM_UNVERIFIED, Verdict.TIMEOUT,
                     Verdict.VERIFIER_TIMEOUT,
                     Verdict.FOUND_INVALID_SAT, Verdict.FALSE_UNSAT,
                     Verdict.CRASH]
    vmap = {v: i for i, v in enumerate(verdict_order)}

    data = np.full((len(instance_ids), len(solvers)), len(verdict_order), dtype=int)
    for i, iid in enumerate(instance_ids):
        for j, solver in enumerate(solvers):
            full_id = f"{iid}_{solver}" if not iid.endswith(f"_{solver}") else iid
            for r in results:
                if r.instance_id == full_id:
                    data[i, j] = vmap.get(r.verdict, len(verdict_order) - 1)
                    break

    fig, ax = plt.subplots(figsize=(max(8, len(solvers) * 2), max(6, len(instance_ids) * 0.25)))
    from matplotlib.colors import ListedColormap
    verdict_colors = [
        "#2ecc71",   # 0 FOUND_VALID_SAT → green
        "#3498db",   # 1 CORRECT_UNSAT_SMALL → blue
        "#85c1e9",   # 2 UNSAT_CLAIM_UNVERIFIED → light blue
        "#f39c12",   # 3 TIMEOUT → orange
        "#e67e22",   # 4 VERIFIER_TIMEOUT → dark orange
        "#e74c3c",   # 5 FOUND_INVALID_SAT → red
        "#c0392b",   # 6 FALSE_UNSAT → dark red
        "#8e44ad",   # 7 CRASH → purple
        "#bdc3c7",   # 8 N/A → gray
    ]
    cmap = ListedColormap(verdict_colors)
    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=0, vmax=len(verdict_colors) - 1)

    ax.set_xticks(range(len(solvers)))
    ax.set_xticklabels(solvers, fontsize=9)
    ax.set_yticks(range(len(instance_ids)))
    ax.set_yticklabels([f"#{i}" for i in range(len(instance_ids))], fontsize=6)
    ax.set_title("EXP-004: Verdict matrix", fontsize=12)

    cbar = fig.colorbar(im, ax=ax, ticks=list(range(len(verdict_colors))), shrink=0.6)
    cbar.set_ticklabels([v.value[:20] for v in verdict_order] + ["N/A"])

    plt.tight_layout()
    path = FIGS / "exp004_verdict_matrix.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  [4/4] {path.name}")


if __name__ == "__main__":
    mp.freeze_support()
    run_experiment()
