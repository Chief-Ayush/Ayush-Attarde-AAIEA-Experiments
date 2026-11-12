"""KB curation: add the FAQ phrasings seen in historical (training) queries to each article's sample_questions."""
import json, sys
sys.path.insert(0, "tools")
from train_gen import GROUPS
kb = json.load(open("knowledge_base.json", encoding="utf-8"))
by = {}
for intent, cx, pri, art, route, cores in GROUPS:
    if art:
        by.setdefault(art, []).extend(cores)
for a in kb:
    have = {q.lower().rstrip("?.") for q in a["sample_questions"]}
    for core in by.get(a["article_id"], []):
        if core.lower() not in have:
            a["sample_questions"].append(core[0].upper() + core[1:])
            have.add(core.lower())
    if a["approved"]:
        a["version"] = a["version"] + 1
json.dump(kb, open("knowledge_base.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print({a["article_id"]: len(a["sample_questions"]) for a in kb})
