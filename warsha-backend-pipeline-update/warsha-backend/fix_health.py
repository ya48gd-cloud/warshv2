import re
content = open('app/main.py', encoding='utf-8').read()
health = '\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'
if '@app.get("/health")' not in content:
    content = content + health
    open('app/main.py', 'w', encoding='utf-8').write(content)
    print('Added health endpoint')
else:
    print('Already exists - fixing syntax')
    content = re.sub(r"\n@app\.get\('/health'\)\ndef health\(\): return \{.*?\}\n", health, content)
    open('app/main.py', 'w', encoding='utf-8').write(content)
    print('Fixed')
