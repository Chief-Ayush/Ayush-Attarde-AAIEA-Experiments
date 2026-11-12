# Experiment 1 - Agent-Based Tutoring System

Notebook: [`AAIEA_Exp_1.ipynb`](AAIEA_Exp_1.ipynb) (Google Colab / Jupyter)

A Computer-Engineering tutor agent built with **Google Gemini**, **Pydantic** (structured output schema) and **system prompts**. It combines a regex
input firewall (prompt-injection / harmful-request blocking), intent classification, ambiguity detection, difficulty adaptation
(Beginner / Intermediate / Advanced), response refinement and self-reflection in a single JSON-constrained call.

## Run
1. Open the notebook in Colab (or locally with `pip install -r requirements.txt`).
2. Provide a Gemini API key - **never paste it into the notebook**:
   - Colab: add a secret named `GEMINI_API_KEY` (key icon in the left sidebar) and allow notebook access, or
   - local: `export GEMINI_API_KEY=...` or copy `.env.example` to `.env`.
3. Run all cells.

> The original notebook had the API key hard-coded in cell 3. It has been replaced by the lookup above before publishing.
