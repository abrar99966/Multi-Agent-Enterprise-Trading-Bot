/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*",
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
    // Workspace app (hx design system) — nested globs are listed explicitly so a
    // future change to the generic components glob can't silently drop them.
    "./components/ws/**/*.{js,jsx}",
    "./lib/ws/**/*.{js,jsx}",
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

        /* ---- hx: workspace design system -------------------------------
           Namespaced under `hx` so it can never collide with the legacy
           ink/gold palette that pages/index.js & friends still depend on.
           Contrast ratios below are measured against hx.bg.base (#070a12). */
        hx: {
          bg: {
            sunken: "#04060c",   // page gutter / behind panels
            base: "#070a12",     // app background
            raised: "#0c111c",   // panel surface
            overlay: "#121926",  // dropdowns, drawers, popovers
          },
          panel: "#0a0f1a",      // default Panel fill (between base and raised)
          border: {
            subtle: "rgba(255,255,255,0.07)",
            strong: "rgba(255,255,255,0.14)",
          },
          accent: {              // cyan — primary action / selection / focus
            300: "#67e8f9",
            400: "#22d3ee",      // 10.7:1
            500: "#06b6d4",
            600: "#0891b2",
          },
          pos: { 300: "#6ee7b7", 400: "#34d399", 500: "#10b981", 600: "#059669" },
          neg: { 300: "#fca5a5", 400: "#f87171", 500: "#ef4444", 600: "#dc2626" },
          warn: { 300: "#fcd34d", 400: "#fbbf24", 500: "#f59e0b", 600: "#d97706" },
          info: { 300: "#93c5fd", 400: "#60a5fa", 500: "#3b82f6", 600: "#2563eb" },
          text: {
            hi: "#f2f5fa",   // 18.1:1 — primary copy, values
            mid: "#a8b3c7",  //  9.0:1 — labels, secondary copy
            lo: "#7d8899",   //  5.4:1 — AA body minimum; hints, units
            dim: "#565f70",  //  3.1:1 — AA for >=18.66px or UI chrome ONLY
          },
        },
      },
      fontFamily: {
        display: ["ui-sans-serif", "system-ui", "Inter", "sans-serif"],
        mono: ["ui-monospace", "JetBrains Mono", "Menlo", "monospace"],
        "hx-sans": ["Inter", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        "hx-mono": ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      /* Micro type scale for terminal-density UI. Line heights are locked so
         table rows stay on a predictable grid regardless of content. */
      fontSize: {
        "hx-10": ["10px", { lineHeight: "14px", letterSpacing: "0.04em" }],
        "hx-11": ["11px", { lineHeight: "15px", letterSpacing: "0.02em" }],
        "hx-12": ["12px", { lineHeight: "16px", letterSpacing: "0.01em" }],
        "hx-13": ["13px", { lineHeight: "18px", letterSpacing: "0" }],
        "hx-14": ["14px", { lineHeight: "20px", letterSpacing: "-0.005em" }],
      },
      boxShadow: {
        glow: "0 0 32px -8px rgba(212, 165, 116, 0.35)",
        card: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 32px 64px -32px rgba(0,0,0,0.5)",
        // hx: elevation is carried by hairlines, not blur — keep it near-flat.
        "hx-panel": "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 1px 2px 0 rgba(0,0,0,0.4)",
        "hx-pop": "0 0 0 1px rgba(255,255,255,0.08), 0 16px 40px -12px rgba(0,0,0,0.75)",
      },
      keyframes: {
        // Live-value flash: tints the cell, then decays back to transparent.
        "hx-flash-pos": {
          "0%": { backgroundColor: "rgba(16,185,129,0.28)" },
          "100%": { backgroundColor: "rgba(16,185,129,0)" },
        },
        "hx-flash-neg": {
          "0%": { backgroundColor: "rgba(239,68,68,0.28)" },
          "100%": { backgroundColor: "rgba(239,68,68,0)" },
        },
        "hx-fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        "hx-slide-up": {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "hx-pulse-dot": {
          "0%": { transform: "scale(1)", opacity: "0.5" },
          "100%": { transform: "scale(2.4)", opacity: "0" },
        },
        "hx-shimmer": {
          "0%": { backgroundPosition: "200% 0" },
          "100%": { backgroundPosition: "-200% 0" },
        },
      },
      animation: {
        "hx-flash-pos": "hx-flash-pos 700ms ease-out 1",
        "hx-flash-neg": "hx-flash-neg 700ms ease-out 1",
        "hx-fade-in": "hx-fade-in 140ms ease-out both",
        "hx-slide-up": "hx-slide-up 180ms cubic-bezier(0.2,0.7,0.2,1) both",
        "hx-pulse-dot": "hx-pulse-dot 1.8s ease-out infinite",
        "hx-shimmer": "hx-shimmer 1.4s linear infinite",
      },
      backgroundImage: {
        "grid-faint":
          "linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)",
      },
    },
  },
  plugins: [],
};
