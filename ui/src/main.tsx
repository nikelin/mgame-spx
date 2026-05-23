import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { getApiBase } from "./api";

// Prime apiBase before first render so resolveAssetUrl() works synchronously in components.
getApiBase().finally(() => {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
});
