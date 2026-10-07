// proxy-config Zhihu external links: decode the target once and retain its HTTP(S) scheme.
// QX uses script-echo-response; Surge uses an http-request script. No network calls.
(function () {
  const qx = typeof $task !== "undefined";
  function reply(status, headers, body) {
    return qx ? {status: "HTTP/1.1 " + status + (status === 302 ? " Found" : " Bad Request"), headers, body}
      : {response: {status, headers, body}};
  }
  let result = reply(400, {"Content-Type": "text/plain", "Cache-Control": "no-store"}, "Invalid external-link target");
  try {
    const requestUrl = String($request.url || "");
    if (!/^https:\/\/link\.zhihu\.com\/\?/i.test(requestUrl)) { $done({}); return; }
    const query = requestUrl.slice(requestUrl.indexOf("?") + 1).split("#", 1)[0];
    const targets = query.split("&").filter(part => part.split("=", 1)[0] === "target");
    if (targets.length === 1) {
      const raw = targets[0].slice("target=".length);
      // Already-decoded URLs may contain their own percent escapes; leave those intact.
      const target = /^https?:\/\//i.test(raw) ? raw : decodeURIComponent(raw);
      if (/^https?:\/\/[^/?#]+(?:[/?#]|$)/i.test(target) && !/[\s\\\u0000-\u001f\u007f]/.test(target) &&
          !target.split("/", 3)[2].includes("@")) {
        result = reply(302, {Location: target, "Cache-Control": "no-store"}, "");
      }
    }
  } catch (error) {
    console.log("[Zhihu] Invalid external-link target; redirect refused");
  }
  $done(result);
})();
