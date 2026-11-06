# Extended-dataset evidence roles

These saved experiments remain useful, but they do not satisfy the full evidence-versus-structure protocol and are therefore supporting or boundary analyses rather than core mechanism evidence.

## AIOps2022 exploratory subset

The source contains 300 labels, while the saved isolated experiment evaluates 51 cases from one cloudbed subset. It is retained to show adapter/task sensitivity, not as a cross-dataset confirmation.

| Scope | Comparison | Metric | Before | After | Difference pp | Paired 95% CI pp |
|---|---|---|---:|---:|---:|---|
| global | Containment-only-Mapping-only | top1 | 0.0784 | 0.0784 | 0.00 | [0.00, 0.00] |
| global | Containment-only-Mapping-only | top3 | 0.2745 | 0.1961 | -7.84 | [-19.61, 3.92] |
| global | Containment-only-Mapping-only | top5 | 0.4510 | 0.3529 | -9.80 | [-21.57, 1.96] |
| global | Containment-only-Mapping-only | mrr | 0.2582 | 0.2287 | -2.96 | [-5.74, -0.20] |
| global | Full-Dependency-only | top1 | 0.0784 | 0.0784 | 0.00 | [0.00, 0.00] |
| global | Full-Dependency-only | top3 | 0.2745 | 0.1961 | -7.84 | [-19.61, 3.92] |
| global | Full-Dependency-only | top5 | 0.4510 | 0.3529 | -9.80 | [-23.53, 1.96] |
| global | Full-Dependency-only | mrr | 0.2574 | 0.2280 | -2.95 | [-5.67, -0.06] |
| level | Containment-only-Mapping-only | top1 | 0.4118 | 0.1961 | -21.57 | [-35.29, -7.84] |
| level | Containment-only-Mapping-only | top3 | 0.6863 | 0.6667 | -1.96 | [-9.80, 5.88] |
| level | Containment-only-Mapping-only | top5 | 0.7647 | 0.8039 | 3.92 | [0.00, 9.80] |
| level | Containment-only-Mapping-only | mrr | 0.5774 | 0.4498 | -12.75 | [-21.90, -3.55] |
| level | Full-Dependency-only | top1 | 0.4118 | 0.1961 | -21.57 | [-35.29, -7.79] |
| level | Full-Dependency-only | top3 | 0.6863 | 0.6667 | -1.96 | [-9.80, 5.88] |
| level | Full-Dependency-only | top5 | 0.7647 | 0.8039 | 3.92 | [0.00, 9.80] |
| level | Full-Dependency-only | mrr | 0.5776 | 0.4498 | -12.78 | [-21.82, -3.35] |

## RCAEval RE2 heterogeneous component ablation

The 90-case saved experiment compares BARO-only Service rankings with LogSource/Operation ownership and call relations. It uses forward two-step propagation with lambda=0.05, so it is not pooled with the one-step bidirectional core protocol. The historical case file's `avg5` field equals reciprocal rank; the table below recomputes Avg@5 from the saved rank as the mean of Top-1 through Top-5.

| Component vs BARO only | Metric | BARO | Component | Difference pp | Paired 95% CI pp |
|---|---|---:|---:|---:|---|
| ownership | top1 | 0.3111 | 0.3222 | 1.11 | [0.00, 3.33] |
| ownership | top3 | 0.9333 | 0.8889 | -4.44 | [-11.11, 2.22] |
| ownership | top5 | 1.0000 | 0.9778 | -2.22 | [-5.56, 0.00] |
| ownership | avg5 | 0.7867 | 0.7889 | 0.22 | [-3.33, 3.78] |
| ownership_service_calls | top1 | 0.3111 | 0.3222 | 1.11 | [0.00, 3.33] |
| ownership_service_calls | top3 | 0.9333 | 0.8889 | -4.44 | [-11.11, 2.22] |
| ownership_service_calls | top5 | 1.0000 | 0.9778 | -2.22 | [-5.56, 0.00] |
| ownership_service_calls | avg5 | 0.7867 | 0.7911 | 0.44 | [-2.89, 3.78] |
| ownership_operation_calls | top1 | 0.3111 | 0.3222 | 1.11 | [0.00, 3.33] |
| ownership_operation_calls | top3 | 0.9333 | 0.8889 | -4.44 | [-11.11, 2.22] |
| ownership_operation_calls | top5 | 1.0000 | 0.9778 | -2.22 | [-5.56, 0.00] |
| ownership_operation_calls | avg5 | 0.7867 | 0.7889 | 0.22 | [-3.33, 3.78] |
| full | top1 | 0.3111 | 0.3222 | 1.11 | [0.00, 3.33] |
| full | top3 | 0.9333 | 0.8889 | -4.44 | [-11.11, 2.22] |
| full | top5 | 1.0000 | 0.9778 | -2.22 | [-5.56, 0.00] |
| full | avg5 | 0.7867 | 0.7911 | 0.44 | [-3.11, 4.00] |

## Use in the paper

AIOps2022 is an applicability-boundary result: the partial adapter run did not reproduce a stable containment gain and lacks the controls needed for mechanism classification.
RE2 is a supporting relation-ablation result: adding heterogeneous ownership/call relations changes little relative to a strong BARO ranking and cannot answer the Host/Pod/Service observation-masking question.
Neither result is discarded, but neither is used to claim transferable evidence compensation. Their role is to delimit when the core protocol can and cannot be instantiated from the available graph and labels.
