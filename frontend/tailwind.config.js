/** Design tokens extracted from the PayPilot Figma file (Overview Dashboard + AI Assistant). */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#faf8ff",
        ink: "#131b2e",
        muted: "#45474c",
        nav: { DEFAULT: "#141b2c", text: "#7c8498" },
        primary: { DEFAULT: "#005ab6", dark: "#00458f", container: "#d7e3ff", on: "#001b3f" },
        surface: { low: "#f2f3ff", DEFAULT: "#eaedff", high: "#e2e7ff", highest: "#dae2fd" },
        outline: "#76777d",
        amber: { banner: "#fef3e2", ink: "#92400e", dot: "#d97706", glow: "#f59e0b", text: "#451a03" },
        success: { DEFAULT: "#047857", bright: "#10b981", soft: "#34d399" },
        danger: { DEFAULT: "#ba1a1a", dark: "#93000a", container: "#ffdad6" },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        display: ['"Hanken Grotesk"', "Inter", "sans-serif"],
        label: ["Geist", "Inter", "sans-serif"],
      },
      borderRadius: { xl: "12px", "2xl": "16px" },
      boxShadow: {
        card: "0 1px 1px rgba(0,0,0,0.05)",
        aside: "0 1px 4px rgba(0,0,0,0.04)",
        header: "0 1px 8px rgba(0,0,0,0.04)",
        float: "0 8px 30px rgba(19,27,46,0.12)",
      },
      keyframes: { ping2: { "75%,100%": { transform: "scale(2)", opacity: "0" } } },
      animation: { ping2: "ping2 1.5s cubic-bezier(0,0,0.2,1) infinite" },
    },
  },
  plugins: [],
};
