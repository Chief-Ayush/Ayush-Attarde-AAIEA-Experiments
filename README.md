# Agentic AI Engineering & Enterprise Automation - Experiment Repository

Lab repository for the course **Agentic AI Engineering and Enterprise Automation (AAIEA)**. Each experiment lives in its own folder; notebook
experiments contain the `.ipynb`, application experiments contain the complete codebase with their own README, requirements and tests.

| # | Experiment | Format | Folder |
|---|------------|--------|--------|
| 1 | Agent-Based Tutoring System (Gemini + Pydantic + system prompts) | Notebook | [`Experiment_1_Agent_Based_Tutoring`](Experiment_1_Agent_Based_Tutoring) |
| 2 | Transaction Fraud Detection Agent (deterministic risk engine + LLM explanations) | Codebase (Streamlit) | [`Experiment_2_Transaction_Fraud_Detection_Agent`](Experiment_2_Transaction_Fraud_Detection_Agent) |
| 3 | Clinical Medication Safety & Evidence Agent (tool integration) | Notebook | [`Experiment_3_Clinical_Medication_Safety_Agent`](Experiment_3_Clinical_Medication_Safety_Agent) |
| 4 | Legal Case Precedent & Regulatory Decision Intelligence System (RAG) | Notebook | [`Experiment_4_Legal_Case_Precedent_Intelligence`](Experiment_4_Legal_Case_Precedent_Intelligence) |
| 5 | CartIQ - multi-agent product research & recommendation pipeline (LangGraph) | Codebase (Streamlit) | [`Experiment_5_CartIQ_Ecommerce_Shopping_Agent`](Experiment_5_CartIQ_Ecommerce_Shopping_Agent) |
| 6 | ReviewPilot - pull-request review assistant / developer automation over MCP | Codebase (Streamlit) | [`Experiment_6_ReviewPilot_MCP_Developer_Automation`](Experiment_6_ReviewPilot_MCP_Developer_Automation) |
| 7 | CampusHire - automated recruitment workflow over MCP | Codebase (Streamlit) | [`Experiment_7_CampusHire_Recruitment_Workflow`](Experiment_7_CampusHire_Recruitment_Workflow) |
| 8 | TaskNest SupportDesk - intelligent customer support with escalation over MCP | Codebase (Streamlit) | [`Experiment_8_IntelligentCustomerSupport_SupportDesk`](Experiment_8_IntelligentCustomerSupport_SupportDesk) |

## Running an experiment

Every experiment is independent - create a virtual environment per folder.

```bash
cd Experiment_5_CartIQ_Ecommerce_Shopping_Agent        # any codebase experiment
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest                                                  # tests (see each README for the exact command)
streamlit run Home.py                                   # entry point differs per experiment: see its README
```

Notebook experiments (1, 3, 4) are meant for Google Colab or Jupyter; see the README in each folder.

## API keys and secrets

No credentials are stored in this repository. Experiments that can call an LLM read the key from the environment
(`GEMINI_API_KEY`, optionally via a local `.env`, or a Colab secret). Copy the provided `.env.example` where one exists and fill in your own key.
`.env` and `kaggle.json` are git-ignored.

## Notes

- Experiment 2 can optionally be evaluated on the Kaggle *Credit Card Fraud Detection* CSV (~150 MB). It is not included because GitHub rejects files
  over 100 MB; see that experiment's README.
- Experiment 6 regenerates its benchmark data (`data/`) automatically on first run.
- Runtime artefacts (databases, logs, caches, virtual environments) are intentionally excluded.
