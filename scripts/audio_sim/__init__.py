"""
Acoustic front end for the ANC sim: duct geometry -> impulse responses ->
decay times -> tap counts -> area budget -> measured attenuation.

Everything is meant to be reached through `run_study`; the modules below are
the working parts of it and are split up only to keep each one readable.

    duct    physical model, geometry + reflection coefficient -> h(t)
    decay   h(t) + a decay threshold -> a duration, in taps
    spans   durations + a margin -> tap counts, reconciled against the tile budget
    sweep   run FxLMS across tap counts and measure what attenuation actually costs

Run any of them directly from the repo root, e.g.
    python3 -m scripts.audio_sim.spans
"""
