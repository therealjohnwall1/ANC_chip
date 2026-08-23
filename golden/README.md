# golden

Implementation/testbench of `scripts/ans.py` algorithm with support for fixed point operations and golden
benchmarks to compare against


## Notation

`N_W` = adaptive filter length (`taps` in `ans.py`).
`N_S` = secondary-path estimate length (`len(s_hat_taps)`).
`n` = sample index. `x[n-k]` = the reference sample from `k` samples ago.

## 3. The streaming equations

`ans.py` precomputes the filtered reference over the whole signal before the
loop starts:

```python
x_filt = np.convolve(x, s_hat_taps)[:len(x)]

S_hat FIR    x_f[n] = sum(k=0..N_S-1)  s_hat[k] * x[n-k]         <-- INCLUDES x[n]

W FIR        y[n]   = sum(k=0..N_W-1)  w[k]     * x[n-1-k]       <-- EXCLUDES x[n]

energy       E[n]   = sum(k=0..N_W-1)  x_f[n-1-k]^2

step size    mu[n]  = lr / (E[n] + eps)

update       w[k]  += mu[n] * e[n] * x_f[n-1-k]
```

## Layers
| layer | file | RTL counterpart |
|---|---|---|
| L0 | `fmt.py` | multiplier, rounder, saturator |
| L1 | `mac.py` | MAC datapath, one cycle / one FIR sequence |
| L2 | `fir.py` | `W(z)` and `S_hat(z)` passes |
| L3 | `update.py` | energy accumulator, divide, update stage |
| L4 | `core.py` | top FSM, one `sample_valid_i` pulse |
| -- | `demo.py` | single-sample propagation + float reference |

## Formats

| param | value | backing |
|---|---|---|
| `SAMPLE_W` | Q1.15 | `theory_of_op.md` |
| `COEF_W` | Q1.15 | `theory_of_op.md`, chosen over the 24-bit draft in `r_interfaces.md` |
| `ACC_W` | 64-bit | `theory_of_op.md` |
| energy, reciprocal, `mu` | **open** | no doc-assigned width |

