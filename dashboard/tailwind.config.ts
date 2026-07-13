import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Deep "ink" surfaces + the accent are driven by CSS variables (see
        // globals.css) so the whole app re-skins per THEME ("Mark" arc reactors):
        // Mark 2 cyan (default), Mark 23 gold/red, Mark 29 silver/red, Mark 1
        // bright graphite. `<alpha-value>` keeps `/opacity` utilities working.
        ink: {
          950: "rgb(var(--ink-950) / <alpha-value>)",
          900: "rgb(var(--ink-900) / <alpha-value>)",
          875: "rgb(var(--ink-875) / <alpha-value>)",
          850: "rgb(var(--ink-850) / <alpha-value>)",
          800: "rgb(var(--ink-800) / <alpha-value>)",
          750: "rgb(var(--ink-750) / <alpha-value>)",
          700: "rgb(var(--ink-700) / <alpha-value>)",
          600: "rgb(var(--ink-600) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--accent-rgb) / <alpha-value>)",
          soft: "rgb(var(--accent-soft-rgb) / <alpha-value>)",
          deep: "rgb(var(--accent-deep-rgb) / <alpha-value>)",
          dim: "rgb(var(--accent-dim-rgb) / <alpha-value>)",
        },
        // `white` + the full `zinc` scale are variable-driven too, so the LIGHT
        // Mark (Mark 1) can invert text + hairlines to dark-on-light without any
        // per-component edits. Dark Marks keep Tailwind's original values.
        white: "rgb(var(--white) / <alpha-value>)",
        zinc: {
          50: "rgb(var(--zinc-50) / <alpha-value>)",
          100: "rgb(var(--zinc-100) / <alpha-value>)",
          200: "rgb(var(--zinc-200) / <alpha-value>)",
          300: "rgb(var(--zinc-300) / <alpha-value>)",
          400: "rgb(var(--zinc-400) / <alpha-value>)",
          500: "rgb(var(--zinc-500) / <alpha-value>)",
          600: "rgb(var(--zinc-600) / <alpha-value>)",
          700: "rgb(var(--zinc-700) / <alpha-value>)",
          800: "rgb(var(--zinc-800) / <alpha-value>)",
          900: "rgb(var(--zinc-900) / <alpha-value>)",
          950: "rgb(var(--zinc-950) / <alpha-value>)",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
      boxShadow: {
        glow: "0 0 0 1px rgb(var(--accent-rgb) / 0.25), 0 0 24px -4px rgb(var(--accent-rgb) / 0.45)",
        "glow-sm": "0 0 16px -6px rgb(var(--accent-rgb) / 0.55)",
        card: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.7)",
        "card-hover":
          "0 1px 0 0 rgba(255,255,255,0.05) inset, 0 16px 40px -16px rgba(0,0,0,0.8)",
      },
      backgroundImage: {
        "arc-reactor":
          "radial-gradient(1200px 600px at 18% -8%, rgb(var(--accent-rgb) / 0.10), transparent 60%), radial-gradient(900px 500px at 100% 0%, rgb(var(--accent-deep-rgb) / 0.08), transparent 55%)",
        "accent-line":
          "linear-gradient(90deg, transparent, rgb(var(--accent-rgb) / 0.6), transparent)",
      },
      keyframes: {
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "pulse-glow": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 0 rgb(var(--accent-rgb) / 0.5)" },
          "50%": { opacity: "0.65", boxShadow: "0 0 0 6px rgb(var(--accent-rgb) / 0)" },
        },
        "spin-slow": {
          to: { transform: "rotate(360deg)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-3px)" },
        },
      },
      animation: {
        shimmer: "shimmer 1.6s infinite",
        "pulse-glow": "pulse-glow 2s ease-in-out infinite",
        "spin-slow": "spin-slow 1s linear infinite",
        float: "float 4s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
