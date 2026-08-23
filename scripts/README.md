# Scripts

- `ans.py` - fxlms adaptive filter sim, the core anc algorithm
- `budget_model.py` - area/cycle budget model, relates impulse response duration and taps to tile count
- `gen_noise.py` - generates reference noise and runs it through the primary path FIR
- `noise_constants.py` - empty, placeholder for shared constants
- `range_check.py` - measures fp64 coefficient/accumulator ranges for fixed point sizing
- `tune.py` - sweeps lms step size against the stability bound
- `verify_convention.py` - proves the streaming/tap-line form of fxlms matches ans.py, pins the index convention for golden/
- `wiener.py` - autocorrelation and lms step helpers

## audio_sim

- `__init__.py` - ties duct/decay/spans/sweep together into run_study
- `duct.py` - physical duct/mic geometry model, generates impulse responses
- `decay.py` - finds impulse response decay time from a duct's ir
- `spans.py` - reconciles acoustic tap requirements against the tile budget
- `sweep.py` - runs fxlms across tap counts and M, measures actual attenuation
