export const views = {
  discussionBoard: () => import("./views/BoardView.svelte"),
  discussionTopic: () => import("./views/TopicView.svelte"),
  discussions: () => import("./views/DiscussionsView.svelte"),
};
