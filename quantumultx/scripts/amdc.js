/**
 * proxy-config amdc.js (2026-10-06): returns invalid content to Alibaba apps' plain-HTTP AMDC
 * dispatch. Whether each app falls back to HTTPS hosts must be checked on the device. Written for this repo to replace
 * ddgksf2013's amdc.js (ddgksf2013/Scripts@819a88e0, one line of logic) with the same User-Agent list,
 * kept exactly as the apps send it (Chinese app names are percent-encoded in the UA).
 * A matching UA gets a fixed non-JSON body; anything else, including a missing UA, passes unchanged.
 * Loaded by Quantumult X, Loon and Surge. Do not overwrite in place: publish changes under a new path.
 */
// AMap 高德, Cainiao 菜鸟, %E9%97%B2%E9%B1%BC 闲鱼, %E9%A3%9E%E7%8C%AA%E6%97%85%E8%A1%8C 飞猪旅行,
// %E5%96%B5%E8%A1%97 喵街, %E5%A4%A9%E7%8C%AB 天猫, Alibaba 1688, MovieApp 淘票票, Hema4iPhone 盒马,
// Moon, DMPortal 大麦.
const APPS = /(AMap|Cainiao|%E9%97%B2%E9%B1%BC|%E9%A3%9E%E7%8C%AA%E6%97%85%E8%A1%8C|%E5%96%B5%E8%A1%97|%E5%A4%A9%E7%8C%AB|Alibaba|MovieApp|Hema4iPhone|Moon|DMPortal)/;
const headers = (typeof $request !== "undefined" && $request && $request.headers) || {};
const uaKey = Object.keys(headers).find(key => key.toLowerCase() === "user-agent");
const ua = String(uaKey ? headers[uaKey] || "" : "");
const hit = ua.match(APPS);
if (hit) {
  console.log("proxy-config amdc: dispatch blocked for " + hit[1]);
  $done({ body: "proxy-config-amdc" });
} else {
  $done({});
}
