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
        // Deep "ink" surfaces — near-black with a cool cast.
        ink: {
          950: "#070809",
          900: "#0b0d11",
          875: "#0f1217",
          850: "#13161d",
          800: "#181c25",
          750: "#1f2430",
          700: "#272d3b",
          600: "#343c4e",
        },
        // Arc-reactor cyan — the single, disciplined accent.
        accent: {
          DEFAULT: "#22d3ee",
          soft: "#67e8f9",
          deep: "#0891b2",
          dim: "#0e7490",
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
        glow: "0 0 0 1px rgba(34,211,238,0.25), 0 0 24px -4px rgba(34,211,238,0.45)",
        "glow-sm": "0 0 16px -6px rgba(34,211,238,0.55)",
        card: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.7)",
        "card-hover":
          "0 1px 0 0 rgba(255,255,255,0.05) inset, 0 16px 40px -16px rgba(0,0,0,0.8)",
      },
      backgroundImage: {
        "arc-reactor":
          "radial-gradient(1200px 600px at 18% -8%, rgba(34,211,238,0.10), transparent 60%), radial-gradient(900px 500px at 100% 0%, rgba(8,145,178,0.08), transparent 55%)",
        "accent-line":
          "linear-gradient(90deg, transparent, rgba(34,211,238,0.6), transparent)",
      },
      keyframes: {
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
        "pulse-glow": {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 0 0 rgba(34,211,238,0.5)" },
          "50%": { opacity: "0.65", boxShadow: "0 0 0 6px rgba(34,211,238,0)" },
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
