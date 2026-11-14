# Actual model request samples — third review

Generated on 2026-11-14 from actual CLI `--dump-prompts` dispatches, using the
deterministic mock backend. These files contain the exact message arrays,
token limits and provider parameters passed to the gateway; they are not
handwritten template previews. No live model API was called.

| Task | Local | Hint | Solve |
| --- | --- | --- | --- |
| Math | [math_local.json](math_local.json) | [math_hint.json](math_hint.json) | [math_solve.json](math_solve.json) |
| Code | [code_local.json](code_local.json) | [code_hint.json](code_hint.json) | [code_solve.json](code_solve.json) |
| Paper | [paper_local.json](paper_local.json) | [paper_hint.json](paper_hint.json) | [paper_solve.json](paper_solve.json) |

The selected local sample is the last actual local dispatch, so its state card
shows accumulated feedback. Hint and solve are their first actual dispatches.
All requests from each capture run remain in the ignored `runs/round3_review/`
directory. The JSON `backend` field is `mock`; prices and output quality from
these dry runs do not establish real model cost or effectiveness.

Paper source text legitimately contains the facts being asked about. Previously
generated candidate answers may also be in the current-best or previous-attempt
section. Grading metadata (`target_answer`, `source_file`, `gold_paragraph`,
`split`) is never copied into the question payload. Only `qid`, `type` and
`question` are exposed. Held-out Q3 is visible as a question, but its grading
result is computed only after the model loop ends.

Reproduce from the repository root after `pip install -e ".[dev]"`:

```bash
python docs/prompt_samples/generate.py
```

The generator invokes this CLI command for each of `math`, `code`, `paper`,
copies the selected requests without editing them, and runs all six policies:

```bash
tierlab run --task math --policy tierlab --backend mock --seed 0 \
  --config docs/prompt_samples/config.yaml --dump-prompts \
  --output-dir runs/round3_review
```

`--dump-prompts` alone writes into the run's `prompts/` directory; an optional
path writes to that directory instead. Dumps do not contain API keys. Use a
fresh destination for independent captures to retain their separate histories.

[strategy_comparison.json](strategy_comparison.json) records all 18 results.
Every policy uses [config.yaml](config.yaml): seed 0, a 15-step limit,
`stagnation_patience=4`, `local_patience=1`, K fixed at 1, lessons disabled,
local limit 2048, hint limit 400. This is a controlled request-capture setup;
the project's default adaptive K schedule remains `[1, 2, 4]`.
Code scores and wall times depend on actual subprocess measurements and can
vary between runs. Compare each row's explicit `stop_reason` alongside call counts.
