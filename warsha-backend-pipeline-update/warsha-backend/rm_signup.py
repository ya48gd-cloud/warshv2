content = open('react-src/src/pages/Login.jsx', encoding='utf-8').read()
# Find and remove the signup link
import re
content = re.sub(r'<[^>]*>[^<]*ورشة جديدة[^<]*<[^>]*>[^<]*إنشاء حساب[^<]*<[^>]*>', '', content)
content = re.sub(r'\{.*?signup.*?\}', '', content, flags=re.DOTALL)
open('react-src/src/pages/Login.jsx', 'w', encoding='utf-8').write(content)
print('Done')
