# Findings

Model under test: `claude-haiku-4-5-20251001`, 160 calls (8 variants x 10 cases x temperatures 0.0 and 0.7, one run each), estimated cost about $0.12. Every output was scored 1-5 on relevance, accuracy, tone and completeness using `rubric.md`. Numbers below come from `results/ranked_variants.csv`, `results/ranked_by_task_type.csv` and `results/consistency_deltas.csv`.

## Winning Prompt Pattern

Overall winner: **s4_role**, the news-editor role prompt for summarization, with a mean total of 19.20 out of 20. It was followed closely by **s3_fewshot** (18.95). The best question-answering variant was **q3_fewshot** (18.35), which beat the baseline q1_direct (17.15) by 1.20 points.

Few-shot examples won in both task types, and the reason is visible in the rubric dimensions. In Q&A, q3_fewshot scored 4.45 on relevance and 4.60 on tone, against 3.70 and 3.90 for the baseline, because the single example taught the model to answer in one or two plain sentences instead of headed, padded write-ups. Its completeness (4.80) was only slightly below the baseline (4.90). In summarization, s3_fewshot had the highest accuracy (5.00) and the second-highest completeness (4.75).

s4_role won summarization on tone (4.95) and relevance (4.75), since the editor persona produced wire-service prose with no preamble. It gave up some completeness (4.55 against 4.75 for s3_fewshot), mostly by dropping secondary facts such as the cause of the wildfire and the stranded passengers.

## Task-Type Prompt Sensitivity

Question answering was more sensitive to prompt wording. The spread between the best and worst Q&A variants was 2.80 points (18.35 for q3_fewshot against 15.55 for q4_role), against 1.80 points for summarization (19.20 against 17.40).

The cause is relevance and tone. Q&A relevance ranged from 2.95 to 4.45 and tone from 3.00 to 4.60, while summarization relevance only ranged from 4.00 to 4.75. Accuracy and completeness hardly moved in either task, with Q&A accuracy between 4.50 and 4.65 and completeness between 4.80 and 5.00 for every variant. In other words, wording changed how the model answered far more than what it knew.

## One Surprising Result

The step-by-step prompt q2_stepbystep scored nearly the lowest total in Q&A (15.65). It had the best completeness of any variant (5.00) but the lowest relevance and tone (3.00 each). The reasoning section plus a repeated "Answer:" section made simple questions long, and that cost more under this rubric than the extra reasoning gained.

The most surprising factual finding was case 12 (which planet has the most moons). The step-by-step variant was the only one to answer correctly (Saturn, 146 moons). q1_direct, q3_fewshot and q4_role all led with Jupiter and 95 moons, at both temperatures, scoring 2 on accuracy each time. The q3 outputs then contradicted themselves by saying Saturn had 146. Working through the planets one at a time appears to have helped on a question where the model's first recall was out of date. That single question is the main reason q3_fewshot's mean accuracy (4.50) is below summarization's near-perfect numbers, and it is one case, so it should be read as an observation and not a conclusion.

Temperature mattered very little. Moving from 0.0 to 0.7 changed a variant's mean total by between -0.1 and +0.4. For the top two summarization variants the delta was 0.0 for s4_role and +0.1 for s3_fewshot, and the top Q&A variant q3_fewshot moved by +0.3.

## One Limitation of This Evaluation

The evaluation has a small sample and a single scorer. Each variant was tested on 10 cases at one run per case, so a single case can move a mean noticeably (the Saturn question alone moves q3_fewshot's accuracy by several tenths). Because there is only one run per case and temperature, the run-to-run standard deviation columns in `results/consistency_deltas.csv` are empty, and the temperature comparison is a difference of means and not a measure of repeat-run variability. One scorer applied the rubric, so there is no inter-rater check, and the rubric rewards brevity and formatting (relevance and tone) more than depth, which favors the few-shot and role prompts. The test inputs were short passages and general-knowledge questions, and only one model was tested, so the ranking may not carry over to other tasks or models.
