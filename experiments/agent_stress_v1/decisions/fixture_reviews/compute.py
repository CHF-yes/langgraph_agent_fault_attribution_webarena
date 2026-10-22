import json, sys, re
reviews = json.load(open(sys.argv[1]))["reviews"]
out = [r["author"] for r in reviews
       if "small" in r["text"].lower() and re.search(r"ear cups?", r["text"], re.I)]
print(json.dumps({"authors": out}))
