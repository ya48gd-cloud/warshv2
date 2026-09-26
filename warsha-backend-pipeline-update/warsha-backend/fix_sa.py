content = open('react-src/src/App.jsx', encoding='utf-8').read()
content = content.replace(
    "  if (window.location.pathname === '/super-admin') return <SuperAdmin />",
    "  if (window.location.search.includes('sa=1') || window.location.hash === '#super-admin') return <SuperAdmin />"
)
open('react-src/src/App.jsx', 'w', encoding='utf-8').write(content)
print('Done')
