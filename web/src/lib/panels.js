// Panels that plug-ins show inside a discussion topic of their own kind (TopicKind.panel). Each is a lazily loaded
// component given { id: <the topic's ref>, embedded: true }.
export const panels = {
  ballot: () => import("../features/voting/components/BallotPanel.svelte"),
};
