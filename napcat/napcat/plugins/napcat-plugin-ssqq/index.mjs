const plugin_init = (ctx) => {
  ctx.router.static("/static", "webui/dist");
  ctx.router.page({
    path: "dashboard",
    title: "Stapxs QQ Lite",
    icon: "📱",
    htmlFile: "webui/dist/index.html",
    description: "Stapxs QQ Lite"
  });
};
const index = {
  plugin_init
};

export { index as default, plugin_init };
