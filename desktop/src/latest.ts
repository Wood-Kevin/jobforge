// "Latest request wins" for loaders that can overlap: a slow older answer (the big "Filtered out"
// list) must not replace a newer one (the tab clicked after it). Each call takes a ticket; only the
// newest ticket may apply its result.
export function latestGate() {
  let newest = 0;
  return {
    begin: () => ++newest,
    isCurrent: (ticket: number) => ticket === newest,
  };
}
