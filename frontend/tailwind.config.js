/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "Cascadia Mono", "Consolas", "ui-monospace", "monospace"],
      },
      fontSize: { "2xs": ["0.6875rem", "1rem"] },
      colors: { ink: { 950: "#101113", 900: "#18191c", 700: "#3a3d42", 500: "#6b6f76", 300: "#b9bcc1", 200: "#d9dbde", 100: "#ececee", 50: "#f6f6f7" } },
      borderRadius: { DEFAULT: "3px" },
    },
  },
  plugins: [],
};
