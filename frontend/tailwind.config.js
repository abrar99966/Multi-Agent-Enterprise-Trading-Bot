/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#05070d",
          900: "#0a0e1a",
          850: "#0d1220",
          800: "#121828",
          700: "#1a2236",
          600: "#252e45",
        },
        gold: {
          400: "#e6c181",
          500: "#d4a574",
          600: "#b88654",
        },
        emerald: {
          glow: "#10d995",
        },
        rose: {
          glow: "#f43f5e",
        },
      },
      fontFamily: {
        display: ["ui-sans-serif", "system-ui", "Inter", "sans-serif"],
        mono: ["ui-monospace", "JetBrains Mono", "Menlo", "monospace"],
      },
      boxShadow: {
        glow: "0 0 32px -8px rgba(212, 165, 116, 0.35)",
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 32px 64px -32px rgba(0,0,0,0.5)",
      },
      backgroundImage: {
        "grid-faint":
          "linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)",
      },
    },
  },
  plugins: [],
};
