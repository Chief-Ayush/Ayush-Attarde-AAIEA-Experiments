# Experiment 3 - Clinical Medication Safety & Evidence Agent

Notebook: [`AAIEA_Exp_3.ipynb`](AAIEA_Exp_3.ipynb) (Google Colab / Jupyter, CPU runtime is enough)

A tool-integrated healthcare decision-support agent: goal analysis -> plan -> tool selection -> schema-validated tool calls -> authorization ->
tool gateway -> result validation -> recovery (retry / fallback) -> logging. Tools: **PubMed search**, **OpenFDA drug label**, **RxNorm lookup**
(public NCBI / FDA / NLM endpoints) and a local **BMI calculator**. The reliability evaluation covers task correctness, planning, tool-calling,
workflow, recovery, safety & security and observability.

## Run
Open in Colab and run all cells. Dependencies (installed by the first cell): `requests`, `pandas`. Live API results can vary because the
public services are external; the notebook separates live demonstrations from controlled evaluation cases.
