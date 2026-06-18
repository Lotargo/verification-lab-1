# Architectural Trust and Safety Scope

This document defines the mathematical, physical, and transport boundaries governing the verification lab experiments. The security architecture implements a defense-in-depth model with checks at the algebraic, transport, and symbolic rule layers.

---

## 1. Layers of Defense

Every computational block flowing through the verification pipeline is evaluated at three independent gates:

```
  Input ──► [ Algebraic Guards ] ──► [ Transport Integrity Checks ] ──► [ Policy Gate ] ──► Output
```

1. **Algebraic Guards**: Pre-execution input validation — range checks, finiteness constraints, domain limits — preventing division-by-zero errors and NaN propagation.
2. **Transport Integrity Checks**: Local verification of data completeness and checksum correctness to validate transmission states and reject corrupted payloads.
3. **Policy Gate Check**: Downstream rule-based evaluation validating physics, logic, or constraint-satisfaction targets (e.g., inverse-square laws, SAT assignments).

---

## 2. Scope of Experiments

### EXP-001: Analytical Correctness
- **Verification Domain**: Classical Newtonian physics (universal gravitation).
- **Scope Capping**: Evaluates acceleration values \(g(h)\) from surface level (\(h = 0\)) up to satellite altitudes (\(h = 10^7\) m).
- **Compensated Summation**: Kahan summation to eliminate floating-point absorption errors when adding small height increments to Earth's radius.

### EXP-002: Input Sanitization and Integrity Rejection
- **Verification Domain**: Adversarial input injection.
- **Transport Mutation Scope**:
  - Checksum validation failures (corrupted checksum flags).
  - Length constraint violations (payload boundary mismatches).
- **Physical Boundary Enforcement**: Immediate rejection of negative mass (\(M \le 0\)), negative constants (\(G \le 0\)), subsurface heights (\(h < 0\)), and infinite or NaN values.

### EXP-003: Singular State Classification
- **Verification Domain**: Numerical singularity classification.
- **Approach Limits**: Evaluates approach trends over 15 orders of magnitude (\(10^{-1}\) to \(10^{-15}\)).
- **Policy Restrictions**: Only removable singularities with a verified finite limit are permitted to proceed. Essential singularities, jump discontinuities, and division poles are structurally blocked.

### EXP-004: NP-Complete Constraint Auditing
- **Verification Domain**: Verifiable SAT solving.
- **Solver Verdicts**: Every SAT assignment is parsed and validated clause-by-clause independently of the solver engine.
- **Small-Scale UNSAT Bounds**: Parallel brute-force verification confirms UNSAT claims on instances with \(n \le 20\) variables.
