import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@/styles/globals.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// PWA shell caching — see public/sw.js. Registered only in production
// builds; in dev, Vite's own module graph/HMR should never be intercepted
// by a service worker.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // best-effort — a failed registration should never block the app
    });
  });
}
