// Panels that plug-ins show inside a discussion topic of their own kind (TopicKind.panel). Each is a lazily loaded
// component given { id: <the topic's ref>, embedded: true }.
import { panelContributions } from "virtual:portal-programs";
import { combineContributions } from "./views.js";

export const panels = combineContributions("panel", ...panelContributions);
