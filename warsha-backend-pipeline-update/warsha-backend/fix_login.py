content = open('react-src/src/pages/Login.jsx', encoding='utf-8').read()
lines = content.split('\n')
new_lines = []
for line in lines:
    if 'setShowSignup' in line or 'ورشة جديدة' in line or 'إنشاء حساب' in line:
        continue
    new_lines.append(line)
open('react-src/src/pages/Login.jsx', 'w', encoding='utf-8').write('\n'.join(new_lines))
print('Done, lines removed:', len(lines) - len(new_lines))
