# Evaluation

`evaluation/agentic_search_cases.json` contains 12 synthetic, versioned evaluation cases for the optional agentic-search fallback. Cases cover empty-result recovery, ordinary searches, unsafe prompts, unsupported external requests, and bounded-result behavior.

`evaluation/agentic_search_runner.py` runs the suite deterministically with a fake/offline provider by default. It checks safety and expected retrieval behavior without requiring network access or an API key.

The evaluation data is kept because it is synthetic and useful for regression testing. Historical private source artifacts and real candidate data are not included.
