/** @type {import('tailwindcss').Config} */

// Cool near-black neutrals. Overrides Tailwind's slate so every existing text-slate-* / bg-slate-* class follows.
const neutral = {
  50: "#F7F7F8",
  100: "#EDEDEF",
  200: "#D9D9DE",
  300: "#BDBDC4",
  400: "#9D9DA6",
  500: "#7E7E88",
  600: "#5A5A63",
  700: "#3F3F46",
  800: "#27272A",
  900: "#18181B",
  950: "#0E0E11",
};

// Signal lime: the console accent. 400 is the signal colour itself; fills carry dark text, never white.
const signal = {
  DEFAULT: "#B8FF00",
  hover: "#A3E600",
  subtle: "#1A2400",
  50: "#F7FFE0",
  100: "#EEFFC2",
  200: "#DCFF85",
  300: "#CBFF47",
  400: "#B8FF00",
  500: "#A3E600",
  600: "#86BF00",
  700: "#668F00",
  800: "#4A6600",
  900: "#2E4000",
  950: "#1A2400",
};

// Cyan pulse: the secondary signal (live/active states, warnings, second series).
const pulse = {
  DEFAULT: "#00E5FF",
  50: "#E6FDFF",
  100: "#CCFBFF",
  200: "#99F6FF",
  300: "#66F0FF",
  400: "#00E5FF",
  500: "#00BFD6",
  600: "#0097A8",
  700: "#006F7C",
  800: "#00505A",
  900: "#00363D",
  950: "#001F24",
};

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["IBM Plex Sans", "Segoe UI", "system-ui", "-apple-system", "sans-serif"],
        mono: ["IBM Plex Mono", "Cascadia Mono", "Consolas", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["0.6875rem", "1rem"],
      },
      colors: {
        // One palette everywhere: lime (primary, success), cyan (secondary, warnings), red (errors), neutrals.
        // Tailwind's other hues are remapped so existing classes cannot introduce a fourth colour.
        slate: neutral,
        emerald: signal,
        green: signal,
        amber: pulse,
        yellow: pulse,
        sky: pulse,
        teal: pulse,
        cyan: pulse,
        indigo: neutral,
        violet: neutral,
        purple: neutral,
        blue: pulse,
        app: "#09090B",
        sidebar: "#0C0C0F",
        surface: {
          DEFAULT: "#0F0F13",
          raised: "#141419",
          hover: "#1B1B21",
        },
        border: {
          DEFAULT: "#26262C",
          subtle: "#1C1C21",
        },
        text: {
          primary: neutral[100],
          secondary: neutral[400],
          muted: neutral[500],
        },
        accent: signal,
        pulse,
        status: {
          success: "#B8FF00",
          warning: "#00E5FF",
          danger: "#E5534B",
          info: "#00E5FF",
        },
        ink: {
          950: "#09090B",
          900: neutral[100],
          700: neutral[400],
          500: neutral[500],
          300: "#26262C",
          200: "#1C1C21",
          100: "#0F0F13",
          50: "#09090B",
        },
      },
      borderRadius: {
        // Soft, rounded surfaces.
        DEFAULT: "8px",
        sm: "6px",
        md: "10px",
        lg: "12px",
        xl: "16px",
        "2xl": "20px",
        "3xl": "28px",
      },
    },
  },
  plugins: [],
};
