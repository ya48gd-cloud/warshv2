app = open('react-src/src/App.jsx', encoding='utf-8').read()
if 'SuperAdmin' not in app:
    app = app.replace("import FieldService", "import SuperAdmin from './pages/SuperAdmin'\nimport FieldService")
    app = app.replace("  if (loading) return (", "  if (window.location.pathname === '/super-admin') return <SuperAdmin />\n\n  if (loading) return (")
    open('react-src/src/App.jsx', 'w', encoding='utf-8').write(app)
    print('Done')
else:
    print('Already added')
