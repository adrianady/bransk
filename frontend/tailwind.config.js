/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Source Sans 3 Variable"', '"Source Sans 3"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        serif: ['"Source Serif 4 Variable"', '"Source Serif 4"', 'Georgia', 'serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      colors: {
        // Semantic roles come from CSS variables so both themes share names.
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        'surface-2': 'rgb(var(--surface-2) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        accent: 'rgb(var(--accent) / <alpha-value>)',
        'accent-strong': 'rgb(var(--accent-strong) / <alpha-value>)',
        'accent-soft': 'rgb(var(--accent-soft) / <alpha-value>)',
        'on-accent': 'rgb(var(--on-accent) / <alpha-value>)',
        brass: 'rgb(var(--brass) / <alpha-value>)',
        'brass-soft': 'rgb(var(--brass-soft) / <alpha-value>)',
        verdigris: 'rgb(var(--verdigris) / <alpha-value>)',
        'verdigris-soft': 'rgb(var(--verdigris-soft) / <alpha-value>)',
        madder: 'rgb(var(--madder) / <alpha-value>)',
        'madder-soft': 'rgb(var(--madder-soft) / <alpha-value>)',
        // Lapis ultramarine — the app's one accent voice.
        chrono: {
          50: '#eef3fb',
          100: '#dfe8f7',
          200: '#c3d4ef',
          300: '#9cb8e5',
          400: '#7398d8',
          500: '#4f79c8',
          600: '#2f5fa8',
          700: '#284f8c',
          800: '#234373',
          900: '#1e385f',
          950: '#142542',
        },
        // Mineral neutrals replace the default cold Tailwind gray.
        gray: {
          50: '#f1f4f8',
          100: '#e6ebf2',
          200: '#d4dbe5',
          300: '#b7c1ce',
          400: '#8d99a9',
          500: '#677385',
          600: '#4e5a6b',
          700: '#3a4554',
          800: '#252e3a',
          900: '#161d27',
          950: '#0d1219',
        },
      },
      boxShadow: {
        card: '0 1px 0 rgb(15 23 42 / 0.03), 0 1px 3px rgb(15 23 42 / 0.05)',
        lift: '0 10px 30px -14px rgb(15 23 42 / 0.28)',
        pop: '0 22px 60px -18px rgb(8 15 28 / 0.4)',
      },
      borderRadius: {
        DEFAULT: '8px',
        lg: '10px',
        xl: '14px',
        '2xl': '18px',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'rise-in': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 160ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
        'rise-in': 'rise-in 200ms cubic-bezier(0.2, 0.8, 0.2, 1) both',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
