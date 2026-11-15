import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { loadTheme } from "./lib/theme.svelte.js";

loadTheme();
mount(App, { target: document.getElementById("app") });
