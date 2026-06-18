# Execution Instructions: EXP-004-alpha SAT/3-SAT Harness

Follow these steps to run the experiment and verify reproducibility:

## 1. Setup Virtual Environment
Create and activate a clean Python virtual environment:

### On Windows (CMD/PowerShell)
```bash
python -m venv .venv
.venv\Scripts\activate
```

### On Linux / macOS / WSL
```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2. Install Dependencies
Install required numerical and graphing libraries:
```bash
pip install -r requirements.txt
```

## 3. Run Experiment
Execute the harness using 4 worker processes for brute-force verification:

### On Windows (PowerShell)
```powershell
$env:EXP004_BF_WORKERS="4"; python exp004_sat_challenge.py
```
*(Alternative CMD: `set EXP004_BF_WORKERS=4 && python exp004_sat_challenge.py`)*

### On Linux / WSL / macOS
```bash
EXP004_BF_WORKERS=4 python3 exp004_sat_challenge.py
```

## 4. Expected Output
Upon successful run, the terminal output must show:
```text
Total runs: 58
SAT found & verified: 40
UNSAT (verified or claimed): 18
Timeout (solver): 0
Timeout (verifier): 0
Failures (invalid/false/crash/verifier): 0
Pass rate: 100.0%
```

All 58 test runs across Layers L1 to L4 must output `100.0%` pass rate, and the `exp_docs/` folder will be populated with results (CSV, JSONL), generated DIMACS CNF files in `exp_docs/instances/`, and 4 analytical verification plots in `exp_docs/figures/`.
