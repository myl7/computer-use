# Corrected simulator results

Known-cost scenario simulation. No model calls or new measurement claims.

The four silent-failure cells use the corrected observer that excludes hidden harm from controller savings. All other cells retain their frozen initial source snapshot.

Ratios below compare mean accounted cost with always reactive. Intervals are paired bootstrap intervals over 20 repetitions, including real-stream mapping variation, conditional on measured/imputed cost constants.

| Model | Admission assumption | Stream | Earliest + cap | Success 10 + cap | Break-even + cap | Projected + cap (95% CI) |
|---|---|---|---:|---:|---:|---:|
| DeepSeek | binary_replay | poisson | 1.652 | 1.474 | 1.654 | 1.636 [1.623, 1.651] |
| DeepSeek | binary_replay | zipf | 1.005 | 1.044 | 1.052 | 1.000 [0.979, 1.022] |
| DeepSeek | binary_replay | bursty | 1.561 | 1.452 | 1.561 | 1.547 [1.513, 1.582] |
| DeepSeek | binary_replay | sepsis | 1.052 | 1.030 | 1.048 | 1.047 [1.036, 1.058] |
| DeepSeek | binary_replay | bpi2019 | 0.973 | 0.977 | 0.977 | 0.971 [0.950, 0.991] |
| DeepSeek | binary_replay | wiki_A | 0.967 | 0.973 | 0.972 | 0.966 [0.924, 0.999] |
| DeepSeek | binary_replay | wiki_B | 1.057 | 1.045 | 1.056 | 1.054 [1.052, 1.056] |
| DeepSeek | soft | poisson | 1.248 | 1.157 | 1.230 | 1.241 [1.118, 1.351] |
| DeepSeek | soft | zipf | 0.915 | 0.928 | 0.938 | 0.920 [0.815, 1.025] |
| DeepSeek | soft | bursty | 1.126 | 1.106 | 1.124 | 1.115 [0.991, 1.221] |
| DeepSeek | soft | sepsis | 1.036 | 1.018 | 1.032 | 1.035 [1.024, 1.047] |
| DeepSeek | soft | bpi2019 | 0.928 | 0.934 | 0.939 | 0.929 [0.906, 0.949] |
| DeepSeek | soft | wiki_A | 0.932 | 0.942 | 0.949 | 0.932 [0.884, 0.971] |
| DeepSeek | soft | wiki_B | 1.019 | 1.010 | 1.018 | 1.018 [1.014, 1.021] |
| DeepSeek | uniform | poisson | 0.903 | 0.902 | 0.870 | 0.886 [0.784, 0.994] |
| DeepSeek | uniform | zipf | 0.861 | 0.890 | 0.869 | 0.880 [0.787, 0.968] |
| DeepSeek | uniform | bursty | 0.873 | 0.866 | 0.831 | 0.861 [0.766, 0.961] |
| DeepSeek | uniform | sepsis | 1.009 | 0.999 | 1.007 | 1.009 [0.999, 1.019] |
| DeepSeek | uniform | bpi2019 | 0.599 | 0.707 | 0.688 | 0.608 [0.571, 0.647] |
| DeepSeek | uniform | wiki_A | 0.587 | 0.699 | 0.672 | 0.603 [0.532, 0.679] |
| DeepSeek | uniform | wiki_B | 0.937 | 0.951 | 0.949 | 0.937 [0.932, 0.942] |
| GLM | binary_replay | poisson | 0.781 | 0.888 | 0.830 | 0.795 [0.765, 0.826] |
| GLM | binary_replay | zipf | 0.692 | 0.753 | 0.753 | 0.699 [0.677, 0.721] |
| GLM | binary_replay | bursty | 0.810 | 0.903 | 0.873 | 0.842 [0.790, 0.894] |
| GLM | binary_replay | sepsis | 0.990 | 0.987 | 0.992 | 0.989 [0.979, 1.000] |
| GLM | binary_replay | bpi2019 | 0.416 | 0.607 | 0.466 | 0.417 [0.382, 0.456] |
| GLM | binary_replay | wiki_A | 0.400 | 0.590 | 0.451 | 0.401 [0.344, 0.466] |
| GLM | binary_replay | wiki_B | 0.901 | 0.934 | 0.913 | 0.901 [0.895, 0.906] |
| GLM | soft | poisson | 0.690 | 0.887 | 0.747 | 0.738 [0.681, 0.794] |
| GLM | soft | zipf | 0.761 | 0.815 | 0.828 | 0.789 [0.717, 0.867] |
| GLM | soft | bursty | 0.711 | 0.867 | 0.777 | 0.764 [0.690, 0.841] |
| GLM | soft | sepsis | 0.998 | 0.995 | 0.998 | 0.998 [0.988, 1.009] |
| GLM | soft | bpi2019 | 0.434 | 0.624 | 0.502 | 0.435 [0.402, 0.472] |
| GLM | soft | wiki_A | 0.410 | 0.602 | 0.483 | 0.411 [0.363, 0.467] |
| GLM | soft | wiki_B | 0.907 | 0.938 | 0.918 | 0.906 [0.901, 0.912] |
| GLM | uniform | poisson | 0.834 | 0.977 | 0.866 | 0.885 [0.782, 0.996] |
| GLM | uniform | zipf | 0.931 | 0.942 | 0.987 | 0.965 [0.869, 1.059] |
| GLM | uniform | bursty | 0.820 | 0.924 | 0.841 | 0.869 [0.770, 0.977] |
| GLM | uniform | sepsis | 1.008 | 0.998 | 1.004 | 1.006 [0.997, 1.015] |
| GLM | uniform | bpi2019 | 0.469 | 0.663 | 0.567 | 0.479 [0.449, 0.511] |
| GLM | uniform | wiki_A | 0.446 | 0.653 | 0.554 | 0.453 [0.411, 0.509] |
| GLM | uniform | wiki_B | 0.921 | 0.950 | 0.934 | 0.918 [0.913, 0.922] |

## Interpretation limits

- Admission probabilities are binary-replay, soft (0.8/0.2), or uniform (0.5) scenarios, not repeated-build measurements.
- Costs and hazard are supplied; arrival and admission estimates use only past observations.
- The hard failed-spend check reserves the next failed-attempt cost; no competitive guarantee is asserted.
- Fixed10 starts after ten total family arrivals and remains eligible after a break. Success10 resets its successful-reactive counter after admission or an observed break.
- Real logs supply arrival order only; shuffled family cost mappings are held identical across policies in a repetition.
- All policies have free family-ID lookup, free relisting, common fixed TTL, and the same router-cost scenario.
- Harm penalties are evaluator costs and are not charged API expenditure.
- No paper number or manuscript source was updated by this run.
