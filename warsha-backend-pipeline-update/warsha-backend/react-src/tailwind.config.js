/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['IBM Plex Sans Arabic', 'Tajawal', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      colors: {
        teal:  { DEFAULT: '#0F6E56', lt: '#E1F5EE', md: '#1D9E75', dk: '#0a5a45' },
        amber: { DEFAULT: '#854F0B', lt: '#FAEEDA', md: '#EF9F27' },
        red:   { DEFAULT: '#A32D2D', lt: '#FCEBEB', md: '#E24B4A' },
        blue:  { DEFAULT: '#185FA5', lt: '#E6F1FB', md: '#2475C8' },
        green: { DEFAULT: '#3B6D11', lt: '#EAF3DE', md: '#639922' },
        erp:   { bg: '#f5f4f0', surface: '#ffffff', border: '#e2e0d8', text: '#1a1a18', muted: '#6b6a64' },
      },
      borderRadius: { erp: '8px', 'erp-lg': '12px' },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04)',
        modal: '0 20px 60px rgba(0,0,0,.15)',
        float: '0 8px 24px rgba(0,0,0,.12)',
      },
    },
  },
  plugins: [],
}
