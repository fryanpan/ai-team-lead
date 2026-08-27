#!/usr/bin/env python3
"""Solve for the weight the weekly meter puts on a cache-read token.

`meter_calibration.py` shows the meter is NOT a raw token count -- two intervals
of near-identical volume moved it 6 points and 20 points, and what separated them
was model mix. So the meter is model-weighted, and the open question becomes the
one that actually matters for the token goal: within a model, does a cache-read
token count the same as a fresh input token, or is it discounted?

Fit: pts = sum over models of w_model * (input + 5*output + 1.25*write + a*read)

The 5x and 1.25x are the published output and cache-write multipliers, which hold
across every model Anthropic prices. `a` is the free parameter -- the cache-read
multiplier -- and `w_model` absorbs both the per-model price and the unknown pool
size. For each candidate `a` the model is linear in w, so it is an ordinary least
squares fit; sweeping `a` and taking the residual minimum reads the answer off
the data instead of off a docs page.

  a ~ 0.1  -> reads are discounted the way they are PRICED
  a ~ 1.0  -> a cache read costs a full token against the meter
"""
import json, sys
import numpy as np

GROUPS = ["claude-fable-5", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5"]


def group_of(model):
    for g in GROUPS:
        if model.startswith(g):
            return g
    return None


def components(rows, a):
    """Design matrix X (interval x model) and target y (meter points)."""
    X, y = [], []
    for r in rows:
        if r["pts"] <= 0:
            continue                      # a flat integer meter carries no signal
        row = [0.0] * len(GROUPS)
        for model, c in r["models"].items():
            g = group_of(model)
            if g is None:
                continue
            row[GROUPS.index(g)] += (c["input_tokens"]
                                     + 5.0 * c["output_tokens"]
                                     + 1.25 * c["cache_creation_input_tokens"]
                                     + a * c["cache_read_input_tokens"]) / 1e6
        X.append(row)
        y.append(r["pts"])
    return np.array(X), np.array(y)


def main():
    rows = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "/tmp/meterfit.json"))
    print(f"{len([r for r in rows if r['pts'] > 0])} intervals with meter movement\n")
    print(f"{'a (cache-read weight)':>22} {'residual':>12} {'R^2':>8}   per-model weight")
    best = None
    for a in [0.005, 0.01, 0.02, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]:
        X, y = components(rows, a)
        w, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = float(np.sum((X @ w - y) ** 2))
        r2 = 1 - resid / float(np.sum((y - y.mean()) ** 2))
        flag = ""
        if best is None or resid < best[1]:
            best, flag = (a, resid, w), "  <-- best"
        weights = "  ".join(f"{g.replace('claude-','')}={v:.3f}"
                            for g, v in zip(GROUPS, w))
        print(f"{a:>22.3f} {resid:>12.2f} {r2:>8.3f}   {weights}{flag}")

    a, resid, w = best
    print(f"\nBest fit: cache-read weight a = {a}")

    # DO NOT read the per-model weights as prices. They are regression
    # coefficients on collinear, thinly-sampled columns, and on 2026-08-27 this
    # fit claimed Fable costs 15.1x an Opus 5 token and OPUS 4.8 COSTS 16.9x AN
    # OPUS 5 TOKEN -- two Opus models at near-identical list price. That is a
    # reductio: the coefficients are not prices. Fable's published rate is 2x
    # Opus; use the published rate. Three further tells, all printed below.
    op5 = w[GROUPS.index("claude-opus-5")]
    if op5 > 0:
        print("\nIdentifiability check -- read this before quoting any ratio:")
        for g, v in zip(GROUPS, w):
            print(f"  {g.replace('claude-',''):<12} {v:>8.3f}  = {v/op5:>7.2f}x opus-5")
        print("  Opus 4.8 and Opus 5 are priced within a few percent of each other.")
        print("  If the line above does not say ~1.00x for opus-4-8, the per-model")
        print("  weights are fitting noise and NONE of these ratios is a price.")
    neg = [g for g, v in zip(GROUPS, w) if v < 0]
    if neg:
        print(f"  NEGATIVE weight fitted for {neg} -- physically impossible, so the")
        print("  design matrix is underdetermined. Constrain to w >= 0 before trusting `a`.")


if __name__ == "__main__":
    main()
