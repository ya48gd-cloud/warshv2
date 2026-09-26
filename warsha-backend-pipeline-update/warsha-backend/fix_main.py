content = open('app/main.py', encoding='utf-8').read()
# Remove the wrongly placed health endpoint
content = content.replace('\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n    title=', '\n    title=')
# Add it correctly at the end
if '@app.get("/health")' not in content:
    content += '\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'
open('app/main.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
