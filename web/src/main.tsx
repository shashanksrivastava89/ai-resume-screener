import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { MeProvider } from "./MeContext";
import { RegionLensProvider } from "./RegionContext";
import { ToastProvider } from "./components/overlays";
// The Acme design system tokens, referenced VERBATIM from the vendored copy
// under web/design-system/ (the same files the Claude Design project ships).
import "../design-system/tokens/colors.css";
import "../design-system/tokens/typography.css";
import "../design-system/tokens/spacing.css";
import "../design-system/tokens/base.css";
import "./styles/tokens.css"; // app-only additions (aliases, app bg, keyframes)
import "./styles/app.css";
import { applyTheme, currentTheme } from "./theme";
import { installUiScale } from "./uiScale";

// Apply the saved theme before first paint — no light flash on a dark session.
applyTheme(currentTheme());
// …and the display scale, for the same reason: a viewport-driven decision made after
// the first paint would be a visible jump. Never overrides a person's own browser zoom.
installUiScale();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <MeProvider>
        <RegionLensProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </RegionLensProvider>
      </MeProvider>
    </BrowserRouter>
  </React.StrictMode>
);
