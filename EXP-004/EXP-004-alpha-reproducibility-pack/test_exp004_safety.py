import unittest
import sys
from pathlib import Path

# Add parent directory to path to import esc modules
sys.path.append(str(Path(__file__).resolve().parent))

import exp004_sat_challenge as esc
from exp004_sat_challenge import DIMACS, SatAssignment, Verdict, run_instance

def _mock_bad_chunk(d, s, e, t, w):
    raise ValueError("Simulated worker crash")


class TestExp004Safety(unittest.TestCase):
    def test_invalid_sat_assignment(self):
        # 1. Invalid SAT assignment → FOUND_INVALID_SAT
        dimacs = DIMACS(n_vars=1, n_clauses=1, clauses=[[1]], source="test_invalid")
        dimacs.compute_hash()
        
        original_solve_cdcl = esc.solve_cdcl
        try:
            # Mock solver to return an invalid assignment (x1=False for clause [1])
            def mock_solve_cdcl(d, timeout_ms=5000):
                return esc.SatResult(
                    n_vars=d.n_vars, n_clauses=d.n_clauses,
                    solver_name="cdcl", verdict=Verdict.FOUND_VALID_SAT,
                    assignment=SatAssignment(values={1: False}, n_vars=1)
                )
            esc.solve_cdcl = mock_solve_cdcl
            
            result = run_instance(dimacs, "cdcl", "test_inst", timeout_ms=5000)
            self.assertEqual(result.verdict, Verdict.FOUND_INVALID_SAT)
            self.assertIn("assignment fails", result.error)
        finally:
            esc.solve_cdcl = original_solve_cdcl

    def test_verifier_timeout(self):
        # 2. Brute force verifier timeout → VERIFIER_TIMEOUT
        dimacs = DIMACS(n_vars=2, n_clauses=4, clauses=[[1, 2], [1, -2], [-1, 2], [-1, -2]], source="test_unsat")
        dimacs.compute_hash()
        
        original_solve_cdcl = esc.solve_cdcl
        try:
            def mock_solve_cdcl(d, timeout_ms=5000):
                return esc.SatResult(
                    n_vars=d.n_vars, n_clauses=d.n_clauses,
                    solver_name="cdcl", verdict=Verdict.UNSAT_CLAIM_UNVERIFIED
                )
            esc.solve_cdcl = mock_solve_cdcl
            
            original_bf_oracle = esc.brute_force_oracle
            try:
                # Mock brute_force_oracle to return TIMEOUT
                def mock_bf_oracle(d, timeout_ms=30000):
                    return esc.SatResult(verdict=Verdict.TIMEOUT, metrics=esc.SolverMetrics(wall_time_ns=5000000))
                esc.brute_force_oracle = mock_bf_oracle
                
                result = run_instance(dimacs, "cdcl", "test_inst", timeout_ms=1000)
                self.assertEqual(result.verdict, Verdict.VERIFIER_TIMEOUT)
            finally:
                esc.brute_force_oracle = original_bf_oracle
        finally:
            esc.solve_cdcl = original_solve_cdcl

    def test_verifier_error_worker_crash(self):
        # 3. Worker crash / verifier error → VERIFIER_ERROR
        dimacs = DIMACS(n_vars=14, n_clauses=50, clauses=[[1, 2, 3]], source="test_crash")
        dimacs.compute_hash()
        
        original_solve_cdcl = esc.solve_cdcl
        try:
            def mock_solve_cdcl(d, timeout_ms=5000):
                return esc.SatResult(
                    n_vars=d.n_vars, n_clauses=d.n_clauses,
                    solver_name="cdcl", verdict=Verdict.UNSAT_CLAIM_UNVERIFIED
                )
            esc.solve_cdcl = mock_solve_cdcl
            
            original_chunk = esc._brute_force_chunk
            try:
                esc._brute_force_chunk = _mock_bad_chunk
                
                result = run_instance(dimacs, "cdcl", "test_inst", timeout_ms=5000)
                self.assertEqual(result.verdict, Verdict.VERIFIER_ERROR)
                self.assertIn("Simulated worker crash", result.error)
            finally:
                esc._brute_force_chunk = original_chunk
        finally:
            esc.solve_cdcl = original_solve_cdcl

    def test_empty_clause(self):
        # 4. Empty clause [[]] → CORRECT_UNSAT_SMALL under CDCL
        dimacs = DIMACS(n_vars=1, n_clauses=1, clauses=[[]], source="test_empty")
        dimacs.compute_hash()
        
        result = run_instance(dimacs, "cdcl", "test_inst", timeout_ms=5000)
        self.assertEqual(result.verdict, Verdict.CORRECT_UNSAT_SMALL)

    def test_large_unsat_without_proof(self):
        # 5. Large UNSAT without proof → UNSAT_CLAIM_UNVERIFIED
        dimacs = DIMACS(n_vars=21, n_clauses=1, clauses=[[1], [-1]], source="test_large_unsat")
        dimacs.compute_hash()
        
        original_solve_cdcl = esc.solve_cdcl
        try:
            def mock_solve_cdcl(d, timeout_ms=5000):
                return esc.SatResult(
                    n_vars=d.n_vars, n_clauses=d.n_clauses,
                    solver_name="cdcl", verdict=Verdict.UNSAT_CLAIM_UNVERIFIED
                )
            esc.solve_cdcl = mock_solve_cdcl
            
            result = run_instance(dimacs, "cdcl", "test_inst", timeout_ms=5000)
            self.assertEqual(result.verdict, Verdict.UNSAT_CLAIM_UNVERIFIED)
        finally:
            esc.solve_cdcl = original_solve_cdcl

    def test_hash_mismatch(self):
        # 6. Hash mismatch verification
        dimacs = DIMACS(n_vars=1, n_clauses=1, clauses=[[1]], source="test_hash")
        dimacs.sha256 = "invalid_hash"
        computed = dimacs.compute_hash()
        self.assertNotEqual(computed, "invalid_hash")

    def test_bad_dimacs(self):
        # 7. Bad DIMACS → raise parser error exception
        bad_text = "p cnf bad_vars 2\n1 -2 0\n"
        with self.assertRaises((ValueError, IndexError)):
            esc.parse_dimacs(bad_text)

if __name__ == "__main__":
    unittest.main()
