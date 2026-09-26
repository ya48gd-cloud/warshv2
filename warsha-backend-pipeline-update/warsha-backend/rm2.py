content = open('react-src/src/pages/Login.jsx', encoding='utf-8').read()
# Find the button line and remove it
lines = content.split('\n')
new_lines = [l for l in lines if 'setShowSignup' not in l and 'ورشة جديدة' not in l and 'إنشاء حساب' not in l]
open('react-src/src/pages/Login.jsx', 'w', encoding='utf-8').write('\n'.join(new_lines))
print('Done - removed', len(lines) - len(new_lines), 'lines')
