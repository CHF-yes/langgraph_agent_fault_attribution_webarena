import json, sys, re
products = json.load(open(sys.argv[1]))["products"]
def match(name):
    return (re.search(r"mouth", name, re.I) and re.search(r"guard", name, re.I)
            and re.search(r"bruxism|night|grinding", name, re.I))
chosen = next((p for p in products if match(p["name"])), None)
print(json.dumps({"url": chosen["url"] if chosen else None}))
