/**
 * proxy-config wechat-mp.js (2026-10-24): removes the ad block under WeChat official account articles.
 * Written for this repo from the getappmsgad rule of fmz200's WeChatOfficialAccount.lpx
 * (fmz200/wool_scripts@5d5f63f: response-body-replace-regex advertisement fmz200), run as a script so that
 * a Loon plugin switch can gate it. Every "advertisement" in the body is renamed, so the app finds no ad field.
 * The rule's cps_product_info (商品推广) half is left to blackmatrix7 Advertising.
 * Empty or missing bodies pass unchanged. Loaded by loon/plugins/WeChat.plugin only.
 * Do not overwrite in place: publish changes under a new path.
 */
const body = typeof $response !== "undefined" && $response ? $response.body : undefined;
if (typeof body === "string" && body) {
  $done({ body: body.replace(/advertisement/g, "fmz200") });
} else {
  $done({});
}
