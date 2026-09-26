content = open('react-src/src/pages/Login.jsx', encoding='utf-8').read()
print(content[content.find('signup'):content.find('signup')+200] if 'signup' in content else 'not found')
print(content[content.find('إنشاء'):content.find('إنشاء')+200] if 'إنشاء' in content else 'not found ar')
