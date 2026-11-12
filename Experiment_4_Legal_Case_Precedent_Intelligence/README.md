# Experiment 4 - Legal Case Precedent & Regulatory Decision Intelligence System

Notebook: [`AAIEA_Exp_4.ipynb`](AAIEA_Exp_4.ipynb) (Google Colab recommended)

A modular legal-document retrieval pipeline over real Indian Supreme Court judgment PDFs: PDF parsing (`pypdf`), chunking, semantic retrieval
(`sentence-transformers` + FAISS), filtering and ranking, grounding / source validation, confidence scoring, retry, security checks, case
comparison, conflict screening, audit logging and evaluation.

## Run
1. Open in Colab.
2. The notebook downloads the judgments dataset from Kaggle. When prompted, upload **your own** `kaggle.json` (Kaggle -> Settings -> API ->
   Create New Token). Do not commit `kaggle.json` to the repository.
3. Run all cells (the first code cell installs `kaggle pypdf python-docx sentence-transformers faiss-cpu`).
