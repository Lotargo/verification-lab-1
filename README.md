# Verification Lab

This repository hosts a series of isolated, reproducible experiments demonstrating verification and integrity guarantees of the retrieval pipeline.

Each subdirectory contains a standalone reproducibility pack with executable code, results, figures, and dependency specifications.

---

## Laboratory Experiment Index

### [EXP-001: Gravitational field verification](./EXP-001)
- **Objective**: Validate the analytical computation pipeline from formula derivation to integrity-verified output.
- **Methodology**: Computes gravitational acceleration \(g(h)\) at height \(h\) using Newton's law of universal gravitation, with Kahan-compensated summation for numerical stability and symbolic verification of inverse-square law constraints.
- **Key Artifacts**: [EXP-001-reproducibility-pack.zip](./EXP-001/EXP-001-reproducibility-pack.zip)

### [EXP-002: Adversarial input and integrity checks](./EXP-002)
- **Objective**: Stress-test the pipeline rejection robustness under corrupt, unphysical, or adversarial inputs.
- **Methodology**: Injects 26 distinct failure vectors — including bit-flipped CRCs, corrupted payload lengths, negative mass constants, and subsurface coordinates — and verifies clean rejection before semantic evaluation.
- **Key Artifacts**: [EXP-002-reproducibility-pack.zip](./EXP-002/EXP-002-reproducibility-pack.zip)

### [EXP-003: Typed handling of singular states](./EXP-003)
- **Objective**: Classify and recover from singular division-by-zero states.
- **Methodology**: Applies a multi-order numerical limit oracle analyzing left/right approaches to distinguish poles, indeterminate forms, jump discontinuities, and removable algebraic singularities.
- **Key Artifacts**: [EXP-003-reproducibility-pack.zip](./EXP-003/EXP-003-reproducibility-pack.zip)

### [EXP-004: Verifiable SAT/3-SAT solver challenge](./EXP-004)
- **Objective**: Audit NP-complete solver instances with independently verified SAT assignments.
- **Methodology**: Implements DPLL and CDCL solvers with clause-by-clause SAT assignment verification and a multi-process parallel brute-force verifier for small UNSAT bounds.
- **Key Artifacts**: [EXP-004-alpha-reproducibility-pack.zip](./EXP-004/EXP-004-alpha-reproducibility-pack.zip)

---

## Safety and Scope Boundaries

For a detailed breakdown of mathematical assumptions, verification policies, and security scopes governing these experiments, see [SAFETY_SCOPE.md](./SAFETY_SCOPE.md).

## License

This laboratory repository is licensed under the MIT License. See [LICENSE](./LICENSE) for details.
