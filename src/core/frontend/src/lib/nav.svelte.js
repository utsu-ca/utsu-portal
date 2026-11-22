// The view currently shown in the portal. Views navigate with `go(name, props)`; `seq` makes
// navigating to the same view again reload it.
export const nav = $state({ name: "auth", props: {}, seq: 0 });

export function go(name, props = {}) {
  nav.name = name;
  nav.props = props;
  nav.seq++;
}
