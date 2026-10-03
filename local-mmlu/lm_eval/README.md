# lm-evaluation-harness integration

```bash
pip install lm-eval
python tools/build_hf.py --out out/hf --include-samples
python lm_eval/generate_tasks.py --local out/hf --include-samples      # or --hf-repo your-org/local-mmlu
lm_eval --model hf --model_args pretrained=Qwen/Qwen2.5-0.5B --tasks local_mmlu_ja --include_path lm_eval/tasks
```

Why not fork `ievals`? Its prompt wording and answer regexes are hard-coded in Chinese. Here every
language-specific string lives in `prompts/{lang}.yaml`, `utils.py` builds the prompt functions from
those files, and `generate_tasks.py` emits one YAML per subject plus a group per language, so the same
model can be scored on all seven languages with one command.

For generative (chain-of-thought) evaluation use `tools/common.py:extract_answer`, which applies the same
per-language regex list.
