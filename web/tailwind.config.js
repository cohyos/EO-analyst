/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Heebo", "Assistant", "system-ui", "sans-serif"],
        mono: [
          "IBM Plex Mono",
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "monospace",
        ],
      },
      colors: {
        bg: "var(--bg)",
        "bg-raised": "var(--bg-raised)",
        "bg-sunken": "var(--bg-sunken)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        fg: "var(--fg)",
        "fg-muted": "var(--fg-muted)",
        "fg-dim": "var(--fg-dim)",
        accent: {
          DEFAULT: "var(--accent)",
          fg: "var(--accent-fg)",
          muted: "var(--accent-muted)",
        },
        hot: {
          DEFAULT: "var(--hot)",
          fg: "var(--hot-fg)",
          muted: "var(--hot-muted)",
        },
        level: {
          red: "var(--level-red)",
          "red-bg": "var(--level-red-bg)",
          orange: "var(--level-orange)",
          "orange-bg": "var(--level-orange-bg)",
          yellow: "var(--level-yellow)",
          "yellow-bg": "var(--level-yellow-bg)",
          archive: "var(--level-archive)",
          "archive-bg": "var(--level-archive-bg)",
        },
        ok: "var(--ok)",
        warn: "var(--warn)",
        danger: "var(--danger)",
      },
      boxShadow: {
        panel: "0 1px 2px rgba(0,0,0,0.3), 0 0 0 1px var(--border)",
      },
    },
  },
  plugins: [],
};
