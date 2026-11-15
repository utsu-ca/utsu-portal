import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { svelteTesting } from "@testing-library/svelte/vite";
import tailwindcss from "@tailwindcss/vite";

const here = path => fileURLToPath(new URL(path, import.meta.url));

// The build is committed to src/utsu_core/static so that `pip install` and the Docker image need no Node.
// Rebuild with `npm run build` in web/ after changing anything under web/.
export default defineConfig({
  base: "/static/",
  plugins: [tailwindcss(), svelte(), svelteTesting()],
  build: {
    outDir: here("../src/utsu_core/static"),
    emptyOutDir: true,
    assetsInlineLimit: 0,  // the CSP only allows same-origin files, so even tiny fonts must not become data: URLs
    rollupOptions: {
      input: {
        index: here("index.html"),
        checkin: here("checkin.html"),
        finance: here("finance.html"),
        sso: here("sso.html"),
      },
    },
  },
  server: { proxy: { "/api": "http://localhost:8000", "/sso": "http://localhost:8000", "/branding": "http://localhost:8000" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.js"],
    include: ["src/**/*.test.js"],
  },
});
