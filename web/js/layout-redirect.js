// Classic script loaded in <head> of the Workbench Console: when the operator last
// chose the Editorial layout, go there before the Workbench paints.
try {
  if (localStorage.getItem("agent-hub-layout") === "editorial") {
    location.replace("/editorial/" + location.hash);
  }
} catch (error) {
  /* ignore */
}
