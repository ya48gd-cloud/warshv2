import os, re

path = 'migrations/versions'
files = os.listdir(path)

pattern = r'cols = \[r\[1\] for r in conn\.execute\(sa\.text\(f"PRAGMA table_info\(\{table!r\}\)"\)\)\.fetchall\(\)\]\s+return col in cols'
replacement = 'result = conn.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name=\'" + table + "\' AND column_name=\'" + col + "\'"))\n    return result.fetchone() is not None'

count = 0
for fname in files:
    if not fname.endswith('.py'):
        continue
    fpath = os.path.join(path, fname)
    c = open(fpath, encoding='utf-8').read()
    new = re.sub(pattern, replacement, c)
    if new != c:
        open(fpath, 'w', encoding='utf-8').write(new)
        print('Fixed:', fname)
        count += 1

print('Total fixed:', count)
