/**
 * proxy-config 12306-ads.js (2026-10-25): answers 12306's ad-slot request (ad.12306.cn getAdList) locally,
 * loaded as a Quantumult X script-analyze-echo-response, so the request never reaches the server.
 * Readable hosted copy of ddgksf2013's 12306.js (ddgksf2013/Scripts@14f55c382e175702e65fe0bc2141c7b108c43e2a,
 * SHA-256 4723bc2236835eea0a8b79529c832a9af1cfd37fa1b160becb3a4b51f9067cd5, one line of logic). The three
 * answers are byte-identical to the original's for string placement numbers. Changes: a missing, empty or
 * non-JSON body, or a non-string placementNo (the original compared loosely, so the number 7 matched "0007"),
 * gets the no-ad answer; the original threw before $done and left the request hanging. $done runs once.
 * Do not overwrite in place: publish changes under a new path.
 */
function answer(placement) {
  if (placement === "0007") {
    // Splash slot: one placeholder material with a 1 s skip time, as in the original.
    return { materialsList: [{ billMaterialsId: "6491", filePath: "ddgksf2013", creativeType: 1 }], advertParam: { skipTime: 1 }, code: "00" };
  }
  if (placement === "G0054") return { code: "00", materialsList: [{}] };
  return { code: "00", message: "无广告返回" };
}

let placement = null;
try {
  const request = JSON.parse((typeof $request !== "undefined" && $request && $request.body) || "");
  if (request && typeof request.placementNo === "string") placement = request.placementNo;
} catch (e) {
  console.log("proxy-config 12306: unreadable ad-slot request, answering no ad");
}
const body = JSON.stringify(answer(placement));
if (typeof $task !== "undefined") {
  $done({ body });
} else {
  $done({ response: { body } });
}
