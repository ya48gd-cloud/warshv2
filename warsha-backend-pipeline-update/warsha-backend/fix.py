import re
f = open('migrations/versions/0012_workorder_customer_location.py', encoding='utf-8')
c = f.read()
f.close()
c = re.sub(
    r'cols = \[r\[1\] for r in conn\.execute\(sa\.text\(f"PRAGMA table_info\(\{table!r\}\)"\)\)\.fetchall\(\)\]\s+return col in cols',
    'result = conn.execute(sa.text("SELECT column_name FROM information_schema.columns WHERE table_name=\'" + table + "\' AND column_name=\'" + col + "\'"))\n    return result.fetchone() is not None',
    c
)
f = open('migrations/versions/0012_workorder_customer_location.py', 'w', encoding='utf-8')
f.write(c)
f.close()
print('Done')
