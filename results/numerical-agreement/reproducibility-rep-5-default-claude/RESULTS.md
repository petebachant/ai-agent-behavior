# Elastic modulus from tensile test data

## Summary

| Material | E (GPa), mean of specimens | Specimen SD (GPa) | 95% CI, specimen-level (GPa) | Typical handbook value |
|---|---|---|---|---|
| Aluminum | **69.4** | 1.8 | ±4.4 | ~69–70 |
| Titanium | **116.5** | 1.7 | ±4.2 | ~105–120 |
| Steel | **199.1** | 2.0 | ±4.9 | ~190–210 |

All three moduli match standard values for these material classes. The three specimens of each material agree to within 1–2.5% (coefficient of variation).

![Stress–strain data with linear fits, and per-specimen moduli](modulus_fits.png)

## Data

`measurements.csv` has 90 points: 3 specimens each of aluminum (AL-1…3), steel (ST-1…3) and titanium (TI-1…3). Each specimen has 10 points at strains from 0.02% to 0.20%, with stress in MPa. No values were missing or malformed.

## Method

- For each specimen, fit stress = E·strain + b by ordinary least squares. The slope is the elastic modulus, converted from MPa to GPa (÷1000).
- For each material:
  - **Mean of the three specimen moduli.** This is the headline value. Its 95% CI uses the specimen-to-specimen spread (t with 2 degrees of freedom), which is the more conservative of the two uncertainty estimates.
  - **Pooled fit** through all 30 points. The slope is essentially the same as the specimen mean. Its 95% CI on the slope (±1.1, ±2.1 and ±3.1 GPa for Al, Ti and steel) treats every point as independent, so it ignores variation between specimens and understates the true uncertainty.
- **Linearity check:**
  - Intercepts are all within ±2.3 MPa of zero.
  - R² ≥ 0.998 for every specimen.
  - Adding a quadratic term was not significant for any material (|t| < 1.3).

  So the whole 0–0.2% strain range appears to be in the linear-elastic region, and all points were used.

## Per-specimen results

| Specimen | E (GPa) | ±95% CI of slope | Intercept (MPa) | R² |
|---|---|---|---|---|
| AL-1 | 70.1 | 1.3 | −0.79 | 0.9995 |
| AL-2 | 67.4 | 1.9 | 0.86 | 0.9988 |
| AL-3 | 70.6 | 1.7 | −1.00 | 0.9991 |
| TI-1 | 114.6 | 4.4 | −0.02 | 0.9978 |
| TI-2 | 117.8 | 3.8 | −2.22 | 0.9985 |
| TI-3 | 117.2 | 3.5 | −0.08 | 0.9987 |
| ST-1 | 201.2 | 5.6 | −1.51 | 0.9988 |
| ST-2 | 197.3 | 6.0 | 1.18 | 0.9986 |
| ST-3 | 198.7 | 6.6 | 2.19 | 0.9984 |

AL-2 is the only specimen whose CI barely misses its material's pooled value, at about 3% low. That is a small difference and not enough to call it an outlier.

## Caveats

- Three specimens per material give only a rough estimate of the spread between specimens. The specimen-level CIs are wide because t₀.₉₇₅,₂ = 4.3.
- The scatter around the fit grows with stress, from about 2 MPa for aluminum to about 10 MPa for steel. This is consistent with measurement noise roughly proportional to load. It doesn't bias the slope, but a weighted fit would give slightly tighter per-specimen CIs.
- No information about alloy, temper, or test standard was provided, so the handbook comparison is only indicative.

## Reproducing

```
uv run --no-project --with numpy,scipy,matplotlib python analyze.py
```

This writes `results.csv` (all fit parameters) and `modulus_fits.png`.
