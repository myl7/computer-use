# Frozen three-model revision simulation

Completed 109 cells with 20 paired repetitions and eight policies per cell.

All token ratios use pure model token expenditure. The engine's silent-failure penalty is reported separately.

## Frozen design

- The measured cost profiles and scenario hazard are supplied. Only arrival and admission rates are estimated online.
- Admission scenarios are assumptions, not independent-build probability estimates. binary_replay repeats observed admission status, including no-admission for the terminated Qwen Calc cell.
- Unadmitted profiles receive admitted-model-median C/d/q counterfactuals, even when an upstream row already contains a fill.
- Observed failed costs are retained. Missing failed costs use the model median over complete failed builds only. Qwen Calc partial spend is excluded from this median.
- GLM has no complete failed build. Its base C_fail equals its own C, with predeclared 0.5C and 5C sensitivities on soft/bursty and soft/bpi2019.
- Qwen Calc uses observed partial/censored spend as its own failure charge. The full failed-build cost is unknown. Qwen Web is excluded because refusal left no retained cost profile.
- Exploration success probability is successes divided by completed exploration attempts, not by three task bindings.
- Family IDs and archived-artifact lookup are supplied without error. Router fees are scenario inputs.
- Real logs supply only an arrival order and family recurrence. GUI costs are shuffled onto their IDs each repetition and paired across policies.
- Synthetic poisson is the inherited uniform family-label sampler at fixed length, not a model of wall-clock interarrival times.
- Compile coins use seed/family/attempt. Service coins use seed/family/arrival/channel. This preserves common random numbers across policies.
- Bootstrap variation covers synthetic arrivals, real-stream cost mappings, and engine outcomes. It does not include uncertainty in measured costs or new real-stream samples.
- The spending cap reserves the next failed-attempt cost. The full online rule has no claimed competitive ratio.
- The 63 base, 42 standard sensitivity, and four GLM failed-cost cells were fixed before running this study. Negative results are retained.

## Policy definitions

- `breakeven_cap`: Compile when accumulated positive expected per-use savings cover estimated successful admission cost, subject to the cap.
- `earliest`: After three completed reactive attempts, compile at the first eligible arrival and after each later eligible failure or break.
- `earliest_cap`: Use earliest eligibility and reserve the next failed-build cost under the shared spending cap.
- `fixed10_cap`: After three completed reactive attempts and ten observed family arrivals, compile subject to the cap.
- `projected`: Compile when estimated future-use savings exceed estimated admission cost plus the idle-residency router allowance.
- `projected_cap`: Use projected eligibility and reserve the next failed-build cost under the shared spending cap.
- `reactive`: Serve every arrival with the agent. Pay the same empty-manifest router fee.
- `shared_protocol`: Serve first, then decide. The compiled program is first usable on a later arrival. Three completed reactive attempts, including fallbacks, are required. All policies use the same fixed idle TTL and free archived-artifact relisting. All decisions use observations through the current arrival and supplied scenario costs/hazard.
- `success10_cap`: After three completed reactive attempts and ten successful reactive attempts since admission, compile subject to the cap.

## Reporting conventions

- Each stream cell receives equal weight when averaging normalized cell ratios. Replicate resampling is shared across all cells in an aggregate. Absolute price-weighted token units are never pooled across models.
- Engine harm_tokens: penalty * c per silent failure. This is not model token expenditure.
- Original engine field, which includes harm_tokens. Preserved unchanged inside each row's engine object.
- 95% paired percentile bootstrap with 2000 resamples of repetition indices. All policy outcomes stay paired. Best-comparator intervals reselect the lowest mean comparator within every resample.
- token_cost + auxiliary_penalty, an explicitly assumed loss scale.
- Engine successes / stream arrivals, including unsuccessful reactive attempts and silent failures.
- Ratio of arithmetic mean token costs across the 20 paired repetitions, not the mean of repetition ratios.
- Sum of reactive_tokens, extraction_tokens, compile_tokens, router_tokens, in price-weighted input-token units.
- Best fixed compares `reactive` and `earliest`. Best other rule compares all seven other policies. Both select the lowest mean cost after the study and are descriptive references.
- Confidence intervals are descriptive and are not adjusted for multiple comparisons.

## Base cells

`PC` means projected_cap. Ratios below one favor PC. Quality deltas are percentage points relative to reactive.

| Model | Admission | Stream | PC / reactive [95% CI] | PC / best fixed [95% CI] | Best fixed | PC / best other [95% CI] | Best other | Quality delta [95% CI] |
|---|---|---|---:|---:|---|---:|---|---:|
| GLM | binary_replay | poisson | 0.2501 [0.2376, 0.2635] | 1.2626 [1.2280, 1.3018] | earliest | 1.2626 [1.2280, 1.3018] | earliest | 10.2000 [9.4167, 11.0000] |
| GLM | binary_replay | zipf | 0.2995 [0.2875, 0.3134] | 1.2501 [1.2170, 1.2845] | earliest | 1.2501 [1.2170, 1.2845] | earliest | 7.2333 [6.6000, 7.8504] |
| GLM | binary_replay | bursty | 0.2516 [0.2376, 0.2664] | 1.3327 [1.2847, 1.3883] | earliest | 1.3327 [1.2847, 1.3883] | earliest | 11.7333 [9.4833, 13.8333] |
| GLM | binary_replay | sepsis | 0.9466 [0.9338, 0.9583] | 0.9893 [0.9857, 0.9927] | earliest | 1.0000 [1.0000, 1.0000] | projected | 0.3143 [0.0095, 0.7381] |
| GLM | binary_replay | bpi2019 | 0.1612 [0.1508, 0.1718] | 1.0200 [1.0190, 1.0211] | earliest | 1.0200 [1.0190, 1.0211] | earliest | 8.4982 [5.9783, 11.1082] |
| GLM | binary_replay | wiki_A | 0.1302 [0.1198, 0.1425] | 1.0184 [1.0165, 1.0203] | earliest | 1.0184 [1.0165, 1.0203] | earliest | 7.8554 [4.6083, 11.8672] |
| GLM | binary_replay | wiki_B | 0.7972 [0.7918, 0.8024] | 0.9997 [0.9992, 1.0001] | earliest | 1.0015 [1.0010, 1.0020] | earliest_cap | 2.5715 [2.4085, 2.8020] |
| GLM | soft | poisson | 0.2875 [0.2675, 0.3078] | 1.2311 [1.1866, 1.2794] | earliest | 1.2311 [1.1866, 1.2794] | earliest | 9.6833 [8.9167, 10.4671] |
| GLM | soft | zipf | 0.3420 [0.3179, 0.3672] | 1.2039 [1.1536, 1.2542] | earliest | 1.2039 [1.1536, 1.2542] | earliest | 6.7333 [6.0167, 7.4667] |
| GLM | soft | bursty | 0.2867 [0.2696, 0.3047] | 1.2758 [1.2201, 1.3342] | earliest | 1.2758 [1.2201, 1.3342] | earliest | 11.2333 [8.8833, 13.4833] |
| GLM | soft | sepsis | 0.9586 [0.9448, 0.9721] | 0.9865 [0.9816, 0.9916] | earliest | 1.0000 [1.0000, 1.0000] | projected | 0.2333 [0.0048, 0.6095] |
| GLM | soft | bpi2019 | 0.1775 [0.1664, 0.1889] | 1.0103 [1.0091, 1.0117] | earliest | 1.0103 [1.0091, 1.0117] | earliest | 8.4336 [5.9278, 11.0318] |
| GLM | soft | wiki_A | 0.1456 [0.1342, 0.1586] | 1.0118 [1.0101, 1.0135] | earliest | 1.0118 [1.0101, 1.0135] | earliest | 7.8018 [4.5715, 11.7960] |
| GLM | soft | wiki_B | 0.8065 [0.8013, 0.8117] | 0.9956 [0.9950, 0.9962] | earliest | 1.0000 [1.0000, 1.0000] | projected | 2.5162 [2.3560, 2.7438] |
| GLM | uniform | poisson | 0.3868 [0.3489, 0.4255] | 1.2120 [1.1620, 1.2661] | earliest | 1.2120 [1.1620, 1.2661] | earliest | 8.9333 [7.9167, 9.9167] |
| GLM | uniform | zipf | 0.4507 [0.4122, 0.4894] | 1.1885 [1.1369, 1.2400] | earliest | 1.1885 [1.1369, 1.2400] | earliest | 6.0500 [5.2667, 6.8667] |
| GLM | uniform | bursty | 0.3673 [0.3394, 0.3974] | 1.2182 [1.1612, 1.2812] | earliest | 1.2182 [1.1612, 1.2812] | earliest | 10.4667 [7.9667, 12.8500] |
| GLM | uniform | sepsis | 0.9807 [0.9681, 0.9931] | 0.9807 [0.9708, 0.9931] | reactive | 1.0000 [1.0000, 1.0036] | projected | 0.1952 [0.0000, 0.5286] |
| GLM | uniform | bpi2019 | 0.2243 [0.2116, 0.2374] | 0.9940 [0.9915, 0.9971] | earliest | 1.0001 [1.0000, 1.0001] | projected | 8.2512 [5.7845, 10.8140] |
| GLM | uniform | wiki_A | 0.1920 [0.1784, 0.2067] | 1.0005 [0.9984, 1.0024] | earliest | 1.0005 [1.0000, 1.0024] | earliest | 7.6386 [4.4558, 11.5756] |
| GLM | uniform | wiki_B | 0.8311 [0.8260, 0.8362] | 0.9861 [0.9855, 0.9869] | earliest | 1.0000 [1.0000, 1.0001] | projected | 2.3804 [2.2192, 2.6016] |
| DeepSeek | binary_replay | poisson | 1.6931 [1.6727, 1.7137] | 1.6931 [1.6727, 1.7137] | reactive | 1.6931 [1.6727, 1.7137] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | binary_replay | zipf | 1.2859 [1.2601, 1.3106] | 1.2859 [1.2601, 1.3106] | reactive | 1.2859 [1.2601, 1.3106] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | binary_replay | bursty | 1.6074 [1.5601, 1.6491] | 1.6074 [1.5601, 1.6491] | reactive | 1.6074 [1.5601, 1.6491] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | binary_replay | sepsis | 1.0658 [1.0430, 1.0896] | 1.0658 [1.0430, 1.0896] | reactive | 1.0658 [1.0430, 1.0896] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | binary_replay | bpi2019 | 1.0129 [1.0010, 1.0239] | 1.0129 [1.0010, 1.0239] | reactive | 1.0129 [1.0013, 1.0239] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | binary_replay | wiki_A | 1.0048 [0.9820, 1.0227] | 1.0048 [0.9820, 1.0227] | reactive | 1.0048 [1.0008, 1.0227] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | binary_replay | wiki_B | 1.0755 [1.0737, 1.0771] | 1.0755 [1.0737, 1.0771] | reactive | 1.0755 [1.0737, 1.0771] | reactive | 0.0000 [0.0000, 0.0000] |
| DeepSeek | soft | poisson | 1.1886 [1.0304, 1.3556] | 1.1886 [1.0319, 1.3556] | reactive | 1.1886 [1.0643, 1.3556] | reactive | 1.8833 [1.1333, 2.7833] |
| DeepSeek | soft | zipf | 1.2302 [1.1209, 1.3324] | 1.2302 [1.1209, 1.3324] | reactive | 1.2302 [1.1209, 1.3324] | reactive | 0.4833 [0.1333, 0.9500] |
| DeepSeek | soft | bursty | 1.0780 [0.9097, 1.2455] | 1.0780 [0.9097, 1.2455] | reactive | 1.0780 [1.0265, 1.2455] | reactive | 2.4000 [1.3333, 3.5671] |
| DeepSeek | soft | sepsis | 1.0512 [1.0283, 1.0774] | 1.0512 [1.0283, 1.0774] | reactive | 1.0512 [1.0283, 1.0774] | reactive | 0.0571 [0.0095, 0.1286] |
| DeepSeek | soft | bpi2019 | 0.8947 [0.8522, 0.9351] | 1.0240 [0.9706, 1.0834] | earliest | 1.0318 [1.0203, 1.0834] | projected | 1.7854 [1.1831, 2.5677] |
| DeepSeek | soft | wiki_A | 0.8681 [0.8005, 0.9350] | 1.1065 [1.0124, 1.2075] | earliest | 1.1065 [1.0200, 1.2075] | earliest | 1.8070 [0.8373, 3.0018] |
| DeepSeek | soft | wiki_B | 1.0211 [1.0165, 1.0252] | 1.0211 [1.0165, 1.0252] | reactive | 1.0211 [1.0165, 1.0252] | reactive | 0.6404 [0.5367, 0.7622] |
| DeepSeek | uniform | poisson | 0.7594 [0.6596, 0.8684] | 0.8433 [0.7198, 0.9962] | earliest | 0.9980 [0.9909, 1.0289] | earliest_cap | 5.5167 [3.8667, 7.1667] |
| DeepSeek | uniform | zipf | 1.0418 [0.9178, 1.1735] | 1.0418 [0.9194, 1.1735] | reactive | 1.0418 [1.0064, 1.1735] | reactive | 1.9833 [1.1667, 2.8667] |
| DeepSeek | uniform | bursty | 0.7072 [0.6044, 0.8209] | 0.8451 [0.7345, 0.9757] | earliest | 1.0178 [1.0114, 1.0690] | earliest_cap | 7.3667 [5.1500, 9.5504] |
| DeepSeek | uniform | sepsis | 1.0170 [0.9976, 1.0409] | 1.0170 [0.9976, 1.0409] | reactive | 1.0170 [1.0007, 1.0409] | reactive | 0.1524 [0.0429, 0.3048] |
| DeepSeek | uniform | bpi2019 | 0.5192 [0.4544, 0.5944] | 0.8913 [0.8009, 1.0254] | earliest | 1.1045 [1.0126, 1.2523] | projected | 6.8568 [5.2416, 8.6954] |
| DeepSeek | uniform | wiki_A | 0.4386 [0.3804, 0.5202] | 0.8604 [0.8075, 0.9202] | earliest | 1.0161 [1.0015, 1.0511] | fixed10_cap | 6.2265 [4.2029, 8.7529] |
| DeepSeek | uniform | wiki_B | 0.9080 [0.8976, 0.9180] | 0.9080 [0.8976, 0.9180] | reactive | 1.0025 [1.0012, 1.0041] | projected | 1.8443 [1.6568, 2.0478] |
| Qwen | binary_replay | poisson | 1.4229 [1.3916, 1.4538] | 1.4229 [1.3916, 1.4538] | reactive | 1.4229 [1.3916, 1.4538] | reactive | 0.0000 [0.0000, 0.0000] |
| Qwen | binary_replay | zipf | 1.1550 [1.1253, 1.1803] | 1.1550 [1.1253, 1.1803] | reactive | 1.1550 [1.1303, 1.1817] | reactive | 0.0000 [0.0000, 0.0000] |
| Qwen | binary_replay | bursty | 1.2716 [1.1843, 1.3477] | 1.2716 [1.1843, 1.3477] | reactive | 1.2716 [1.1843, 1.3477] | reactive | 0.0000 [0.0000, 0.0000] |
| Qwen | binary_replay | sepsis | 1.0449 [1.0353, 1.0545] | 1.0449 [1.0353, 1.0545] | reactive | 1.0449 [1.0353, 1.0545] | reactive | 0.0000 [0.0000, 0.0000] |
| Qwen | binary_replay | bpi2019 | 0.7565 [0.6926, 0.8195] | 0.7565 [0.6926, 0.8195] | reactive | 1.0514 [1.0331, 1.0752] | fixed10_cap | 0.0000 [0.0000, 0.0000] |
| Qwen | binary_replay | wiki_A | 0.7136 [0.6544, 0.7741] | 0.7136 [0.6544, 0.7741] | reactive | 1.0522 [1.0311, 1.0785] | fixed10_cap | 0.0000 [0.0000, 0.0000] |
| Qwen | binary_replay | wiki_B | 1.0116 [1.0079, 1.0153] | 1.0116 [1.0079, 1.0153] | reactive | 1.0116 [1.0079, 1.0153] | reactive | 0.0000 [0.0000, 0.0000] |
| Qwen | soft | poisson | 1.1330 [1.0466, 1.2216] | 1.1330 [1.0484, 1.2216] | reactive | 1.1330 [1.0835, 1.2216] | reactive | 1.3500 [0.6000, 2.2667] |
| Qwen | soft | zipf | 1.1813 [1.1216, 1.2400] | 1.1813 [1.1216, 1.2400] | reactive | 1.1813 [1.1328, 1.2424] | reactive | 0.2833 [0.0000, 0.8000] |
| Qwen | soft | bursty | 1.0602 [0.9682, 1.1525] | 1.0602 [0.9688, 1.1525] | reactive | 1.1124 [1.0663, 1.1750] | success10_cap | 0.9667 [0.1167, 2.1175] |
| Qwen | soft | sepsis | 1.0270 [1.0172, 1.0377] | 1.0270 [1.0172, 1.0377] | reactive | 1.0270 [1.0172, 1.0377] | reactive | 0.2333 [0.0714, 0.4381] |
| Qwen | soft | bpi2019 | 0.7217 [0.6759, 0.7701] | 0.9742 [0.9391, 1.0179] | earliest | 1.0254 [1.0158, 1.0434] | fixed10_cap | 0.5552 [0.4506, 0.6821] |
| Qwen | soft | wiki_A | 0.6494 [0.6046, 0.7008] | 1.0161 [0.9755, 1.0731] | earliest | 1.0263 [1.0124, 1.0746] | projected | 0.5687 [0.3782, 0.7960] |
| Qwen | soft | wiki_B | 0.9779 [0.9732, 0.9825] | 0.9779 [0.9732, 0.9825] | reactive | 1.0018 [1.0003, 1.0045] | success10_cap | 0.5326 [0.4245, 0.6673] |
| Qwen | uniform | poisson | 0.9456 [0.8573, 1.0414] | 0.9456 [0.8778, 1.0470] | reactive | 1.0144 [0.9966, 1.0767] | breakeven_cap | 5.1833 [3.4662, 6.8667] |
| Qwen | uniform | zipf | 1.0863 [1.0008, 1.1723] | 1.0863 [1.0008, 1.1723] | reactive | 1.0863 [1.0032, 1.1723] | reactive | 2.3333 [1.2833, 3.3833] |
| Qwen | uniform | bursty | 0.9232 [0.8246, 1.0349] | 0.9232 [0.8326, 1.0355] | reactive | 1.0171 [0.9946, 1.0986] | success10_cap | 5.3000 [2.9163, 7.8837] |
| Qwen | uniform | sepsis | 1.0006 [0.9922, 1.0093] | 1.0006 [0.9922, 1.0093] | reactive | 1.0039 [1.0000, 1.0113] | success10_cap | 0.2619 [0.0905, 0.4762] |
| Qwen | uniform | bpi2019 | 0.6207 [0.5681, 0.6732] | 0.8601 [0.8164, 0.9090] | earliest | 1.0087 [1.0049, 1.0127] | projected | 7.5801 [4.8230, 10.8544] |
| Qwen | uniform | wiki_A | 0.6031 [0.5421, 0.6727] | 0.9359 [0.8768, 1.0044] | earliest | 1.0465 [1.0100, 1.1117] | fixed10_cap | 6.7068 [3.5468, 10.7036] |
| Qwen | uniform | wiki_B | 0.9395 [0.9346, 0.9442] | 0.9395 [0.9346, 0.9442] | reactive | 1.0010 [1.0002, 1.0020] | projected | 1.8389 [1.6311, 2.0574] |

## Sensitivity cells

Auxiliary penalty is a loss-scale value, not tokens. Quality deltas are percentage points.

| Model | Stream | Change | PC / reactive [95% CI] | Quality delta [95% CI] | PC tokens | Auxiliary penalty | Penalized objective / reactive |
|---|---|---|---:|---:|---:|---:|---:|
| GLM | bursty | ttl25 | 0.2861 [0.2689, 0.3040] | 11.2333 [8.8833, 13.4833] | 18229718.2 | 0.0 | 0.2861 |
| GLM | bpi2019 | ttl25 | 0.1732 [0.1624, 0.1842] | 8.4336 [5.9278, 11.0318] | 8536604163.9 | 0.0 | 0.1732 |
| GLM | bursty | ttl400 | 0.2870 [0.2698, 0.3051] | 11.2167 [8.8500, 13.4833] | 18287939.5 | 0.0 | 0.2870 |
| GLM | bpi2019 | ttl400 | 0.1886 [0.1765, 0.2008] | 8.4336 [5.9278, 11.0318] | 9291474240.7 | 0.0 | 0.1886 |
| GLM | bursty | no_router | 0.2834 [0.2662, 0.3014] | 11.2333 [8.8833, 13.4833] | 18030725.2 | 0.0 | 0.2834 |
| GLM | bpi2019 | no_router | 0.1691 [0.1587, 0.1799] | 8.4336 [5.9278, 11.0318] | 8319181562.2 | 0.0 | 0.1691 |
| GLM | bursty | no_drift | 0.2241 [0.2104, 0.2410] | 11.5500 [9.0996, 13.8833] | 14278346.4 | 0.0 | 0.2241 |
| GLM | bpi2019 | no_drift | 0.1094 [0.1000, 0.1189] | 8.6745 [6.1058, 11.3408] | 5392232737.4 | 0.0 | 0.1094 |
| GLM | bursty | high_drift | 0.5389 [0.5041, 0.5769] | 7.7333 [4.6829, 10.6508] | 34341635.9 | 0.0 | 0.5389 |
| GLM | bpi2019 | high_drift | 0.4662 [0.4400, 0.4919] | 5.5599 [3.8423, 7.4761] | 22972019851.2 | 0.0 | 0.4662 |
| GLM | bursty | silent30 | 0.2875 [0.2701, 0.3060] | 9.8333 [7.3825, 12.0333] | 18320788.8 | 1525288.9 | 0.3114 |
| GLM | bpi2019 | silent30 | 0.1754 [0.1645, 0.1865] | 6.7692 [4.3185, 9.2625] | 8643586289.0 | 1380008359.5 | 0.2034 |
| GLM | bursty | price5x | 0.8149 [0.7495, 0.8826] | 3.9500 [2.2667, 5.7171] | 51933273.8 | 0.0 | 0.8149 |
| GLM | bpi2019 | price5x | 0.4161 [0.3870, 0.4448] | 5.7823 [3.8779, 7.9030] | 20503059364.5 | 0.0 | 0.4161 |
| DeepSeek | bursty | ttl25 | 1.0779 [0.9096, 1.2454] | 2.4000 [1.3333, 3.5671] | 53689764.6 | 0.0 | 1.0779 |
| DeepSeek | bpi2019 | ttl25 | 0.8937 [0.8514, 0.9343] | 1.7931 [1.1926, 2.5744] | 35118729735.1 | 0.0 | 0.8937 |
| DeepSeek | bursty | ttl400 | 1.0781 [0.9099, 1.2455] | 2.4000 [1.3333, 3.5671] | 53703943.6 | 0.0 | 1.0781 |
| DeepSeek | bpi2019 | ttl400 | 0.8971 [0.8544, 0.9374] | 1.7842 [1.1824, 2.5667] | 35251221623.4 | 0.0 | 0.8971 |
| DeepSeek | bursty | no_router | 1.0756 [0.9083, 1.2448] | 2.4000 [1.3333, 3.5671] | 53460569.2 | 0.0 | 1.0756 |
| DeepSeek | bpi2019 | no_router | 0.8929 [0.8505, 0.9336] | 1.7931 [1.1926, 2.5744] | 35004363301.7 | 0.0 | 0.8929 |
| DeepSeek | bursty | no_drift | 0.9933 [0.8065, 1.1906] | 3.1667 [1.9667, 4.5500] | 49478556.0 | 0.0 | 0.9933 |
| DeepSeek | bpi2019 | no_drift | 0.2052 [0.1803, 0.2332] | 14.1258 [12.0088, 16.2317] | 8061297688.6 | 0.0 | 0.2052 |
| DeepSeek | bursty | high_drift | 1.0611 [1.0280, 1.0876] | 0.3167 [0.0000, 0.6667] | 52854990.2 | 0.0 | 1.0611 |
| DeepSeek | bpi2019 | high_drift | 1.0091 [1.0080, 1.0103] | 0.0137 [0.0116, 0.0156] | 39652102673.5 | 0.0 | 1.0091 |
| DeepSeek | bursty | silent30 | 1.0798 [0.9145, 1.2435] | 2.3167 [1.2500, 3.4837] | 53784749.5 | 148229.4 | 1.0827 |
| DeepSeek | bpi2019 | silent30 | 0.8980 [0.8523, 0.9399] | 1.4619 [0.8726, 2.2045] | 35284537468.3 | 474853160.1 | 0.9100 |
| DeepSeek | bursty | price5x | 1.1976 [1.1022, 1.2841] | 0.1667 [0.0000, 0.3667] | 59651939.3 | 0.0 | 1.1976 |
| DeepSeek | bpi2019 | price5x | 1.0187 [1.0162, 1.0214] | 0.0276 [0.0211, 0.0343] | 40027577167.2 | 0.0 | 1.0187 |
| Qwen | bursty | ttl25 | 1.0599 [0.9680, 1.1524] | 0.9667 [0.1167, 2.1175] | 26381217.8 | 0.0 | 1.0599 |
| Qwen | bpi2019 | ttl25 | 0.7186 [0.6728, 0.7672] | 0.5552 [0.4507, 0.6821] | 15300000869.8 | 0.0 | 0.7186 |
| Qwen | bursty | ttl400 | 1.0593 [0.9688, 1.1522] | 0.9667 [0.1167, 2.1175] | 26366521.5 | 0.0 | 1.0593 |
| Qwen | bpi2019 | ttl400 | 0.7286 [0.6827, 0.7770] | 0.5414 [0.4301, 0.6751] | 15514307569.6 | 0.0 | 0.7286 |
| Qwen | bursty | no_router | 1.0594 [0.9669, 1.1524] | 0.9667 [0.1167, 2.1175] | 26251119.0 | 0.0 | 1.0594 |
| Qwen | bpi2019 | no_router | 0.7153 [0.6691, 0.7643] | 0.5553 [0.4507, 0.6822] | 15163660007.5 | 0.0 | 0.7153 |
| Qwen | bursty | no_drift | 0.9830 [0.8752, 1.1056] | 2.2833 [0.7333, 4.2171] | 24467464.7 | 0.0 | 0.9830 |
| Qwen | bpi2019 | no_drift | 0.1979 [0.1798, 0.2198] | 8.7910 [5.7941, 12.3466] | 4214265166.3 | 0.0 | 0.1979 |
| Qwen | bursty | high_drift | 1.0268 [1.0139, 1.0358] | 0.0000 [0.0000, 0.0000] | 25557156.9 | 0.0 | 1.0268 |
| Qwen | bpi2019 | high_drift | 1.0043 [1.0039, 1.0049] | 0.0000 [0.0000, 0.0000] | 21384612074.6 | 0.0 | 1.0043 |
| Qwen | bursty | silent30 | 1.0636 [0.9709, 1.1594] | 0.7333 [-0.0833, 1.8667] | 26473637.9 | 253428.5 | 1.0738 |
| Qwen | bpi2019 | silent30 | 0.7190 [0.6731, 0.7680] | 0.1391 [-0.0114, 0.3016] | 15308621143.7 | 283853896.4 | 0.7323 |
| Qwen | bursty | price5x | 1.0753 [1.0292, 1.1198] | 0.0000 [0.0000, 0.0000] | 26762739.0 | 0.0 | 1.0753 |
| Qwen | bpi2019 | price5x | 1.0071 [1.0063, 1.0080] | 0.0000 [0.0000, 0.0000] | 21442729067.6 | 0.0 | 1.0071 |
| GLM | bursty | failure_cost_half | 0.2552 [0.2394, 0.2717] | 11.8500 [9.5496, 14.0000] | 16265914.6 | 0.0 | 0.2552 |
| GLM | bpi2019 | failure_cost_half | 0.1701 [0.1594, 0.1809] | 8.5155 [6.0083, 11.1138] | 8380162079.2 | 0.0 | 0.1701 |
| GLM | bursty | failure_cost_5x | 0.5257 [0.4760, 0.5750] | 6.6167 [4.2163, 8.9838] | 33502598.9 | 0.0 | 0.5257 |
| GLM | bpi2019 | failure_cost_5x | 0.2517 [0.2319, 0.2760] | 6.8222 [4.6815, 9.1577] | 12404030726.0 | 0.0 | 0.2517 |

## Reproduction

```sh
python3 -m unittest discover -s code/t2sim/tests -p 'test*revision*.py'
python3 code/t2sim/run_paper_revision.py --freeze
python3 code/t2sim/run_paper_revision.py --run --workers 6
```

`config.json` freezes all profiles, scenario parameters, source hashes, and baseline definitions. `profile_mapping.json` records every imputation. `cells/` retains all 20 repetitions, engine metrics, stream hashes, and mapping hashes. `summary.json` contains every policy and comparison. No prior result directory is changed.

## Post-run descriptive decomposition

This section uses the frozen outputs. It introduces no additional scenario or causal ablation.

### Main comparison across seven streams

Each entry gives the equal-weight mean of seven per-stream cost ratios to reactive. Reactive is 1 in every row. The interval for projected_cap uses paired bootstrap resampling shared across the seven streams. Intervals for every other rule are retained in summary.json.

| Model / admission | earliest | earliest_cap | fixed10_cap | success10_cap | breakeven_cap | projected | projected_cap [95% CI] |
|---|---:|---:|---:|---:|---:|---:|---:|
| deepseek/deepseek-v4-flash-vision-exp/binary_replay | 5.9433 | 1.2566 | 1.2543 | 1.2159 | 1.2605 | 1.2494 | 1.2494 [1.2403, 1.2578] |
| deepseek/deepseek-v4-flash-vision-exp/soft | 1.2344 | 1.0542 | 1.0437 | 1.0142 | 1.0552 | 1.0424 | 1.0474 [1.0021, 1.0964] |
| deepseek/deepseek-v4-flash-vision-exp/uniform | 0.9278 | 0.7658 | 0.7708 | 0.8128 | 0.7982 | 0.7648 | 0.7702 [0.7329, 0.8135] |
| qwen/qwen3.8-flash/binary_replay | 3.8216 | 1.0438 | 1.0416 | 0.9967 | 1.0724 | 1.0537 | 1.0537 [1.0321, 1.0769] |
| qwen/qwen3.8-flash/soft | 1.1108 | 0.9625 | 0.9565 | 0.9196 | 0.9893 | 0.9654 | 0.9644 [0.9329, 0.9990] |
| qwen/qwen3.8-flash/uniform | 1.0819 | 0.8813 | 0.8860 | 0.9004 | 0.8954 | 0.8724 | 0.8741 [0.8355, 0.9152] |
| z-ai/glm-5.3-flash/binary_replay | 0.3809 | 0.3890 | 0.4568 | 0.5978 | 0.4335 | 0.4052 | 0.4052 [0.4006, 0.4102] |
| z-ai/glm-5.3-flash/soft | 0.4063 | 0.4167 | 0.4746 | 0.6062 | 0.4580 | 0.4292 | 0.4292 [0.4212, 0.4375] |
| z-ai/glm-5.3-flash/uniform | 0.4666 | 0.4801 | 0.5254 | 0.6388 | 0.5203 | 0.4904 | 0.4904 [0.4766, 0.5051] |

With silent=0, a detected program failure uses the same reactive outcome coin as the reactive baseline. A successful program use counts as success. Thus non-decreasing quality in the base study follows from the simulator's construction. It is not independent empirical evidence that the deployed protocol preserves quality.

### DeepSeek binary-replay Poisson cell

Source: `cells/b6e3f624dec48abcc8a8.json`. All entries are means over the same 20 repetitions. Costs are in millions of price-weighted input-token units.

| Policy | Total tokens | Agent | Extraction | Compile | Router | Failed compile | Token ratio to reactive |
|---|---:|---:|---:|---:|---:|---:|---:|
| reactive | 47.411699 | 47.301299 | 0.000000 | 0.000000 | 0.110400 | 0.000000 | 1.000000 |
| earliest | 366.897481 | 40.839766 | 0.086172 | 325.759546 | 0.211997 | 323.727633 | 7.738543 |
| earliest_cap | 81.256018 | 47.187471 | 0.000796 | 33.955895 | 0.111856 | 33.869548 | 1.713839 |
| fixed10_cap | 81.256018 | 47.187471 | 0.000796 | 33.955895 | 0.111856 | 33.869548 | 1.713839 |
| success10_cap | 76.626121 | 47.187471 | 0.000796 | 29.325998 | 0.111856 | 29.239651 | 1.616186 |
| breakeven_cap | 81.040498 | 47.236254 | 0.000452 | 33.692659 | 0.111133 | 33.649486 | 1.709293 |
| projected | 80.273346 | 47.249263 | 0.000366 | 32.912698 | 0.111019 | 32.883915 | 1.693113 |
| projected_cap | 80.273346 | 47.249263 | 0.000366 | 32.912698 | 0.111019 | 32.883915 | 1.693113 |

The failure cost of an already admitted DeepSeek family was not measured. Its scenario value is 2,502,646.76 PW, the median complete failed-build cost from other DeepSeek families. This fill can be much larger than the family's measured successful build cost.

At the initial admission estimate 0.5, estimated admission cost equals C + C_fail. The default hazard caps projected uses at 50. The next table computes the upper bound 50*s from each admitted profile, before the nonnegative router allowance. If this bound is below the initial admission cost, projected eligibility cannot produce a first attempt for that family. The estimate therefore remains 0.5. This is a threshold implication of the fixed inputs and code, not an estimate of a causal effect of imputation.

| Admitted DeepSeek family | C | Imputed C_fail | Initial C+C_fail | Maximum 50*s | Blocks first projected attempt |
|---|---:|---:|---:|---:|---|
| Android/ContactsAddContact | 143912.00 | 2502646.76 | 2646558.76 | 3059439.21 | False |
| Android/MarkorDeleteNote | 68477.00 | 2502646.76 | 2571123.76 | 489676.11 | True |
| Desktop/WriterMemoSave | 585704.47 | 2502646.76 | 3088351.24 | 2453601.09 | True |
| Web/CommentPost | 332356.74 | 2502646.76 | 2835003.50 | 1928990.56 | True |

### Largest projected-cap cost ratio among all 63 base cells

This reporting rule inspects the complete base set. It is a descriptive maximum, with each selected cell's paired bootstrap interval.

| Comparator | Selected cell | Ratio [95% CI] |
|---|---|---:|
| reactive | deepseek/deepseek-v4-flash-vision-exp/binary_replay/poisson/base | 1.693113 [1.672657, 1.713745] |
| best_fixed | deepseek/deepseek-v4-flash-vision-exp/binary_replay/poisson/base | 1.693113 [1.672657, 1.713745] |
| best_other_rule | deepseek/deepseek-v4-flash-vision-exp/binary_replay/poisson/base | 1.693113 [1.672657, 1.713745] |

`component_breakdown.json` preserves unrounded values and the source cell hash. `validation.json` records reconciliation of all 17,440 run records, all source hashes, ratio conventions, and aggregate weights. Recreate this postprocessing with `python3 experimental-results/guiexp/t2_sim_v3/paper_revision_20260922/postprocess_report.py`.
