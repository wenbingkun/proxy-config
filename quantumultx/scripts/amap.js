// AMap cleanup derived from ddgksf2013's amap.js, author @ddgksf2013.
// Source: https://raw.githubusercontent.com/ddgksf2013/Scripts/314f61060a4c72c8a8b9f6b2ff457c8d35abbdac/amap.js
// SHA-256: 936aaaf97cf69b8a5a25ba49303c0470a16ac48851e8bf58080ff9f129ff7c48
// Changes: readable implementation; one completion; malformed bodies pass through;
// preserve current profile business/unknown cards rather than the old two-type allowlist;
// preserve taxi banner metadata while emptying its list; retain observed home-feed cleanup.
// No network requests, persistent state or amdc handling.
(function run() {
  let result = {};
  try {
    const url = String($request.url || "").match(/^https?:\/\/([^/:?#]+)(\/[^?#]*)(?:\?[^#]*)?(?:#.*)?$/);
    if (!url || !url[1].endsWith(".amap.com") || typeof $response.body !== "string") {
      $done(result);
      return;
    }
    const path = url[2], body = JSON.parse($response.body);
    if (!body || typeof body !== "object" || Array.isArray(body)) {
      $done(result);
      return;
    }
    const before = JSON.stringify(body), data = body.data;
    const hasData = data && typeof data === "object" && !Array.isArray(data);
    if (path.startsWith("/ws/valueadded/alimama/splash_screen") && hasData && Array.isArray(data.ad)) {
      for (const item of data.ad) {
        if (!item || typeof item !== "object") continue;
        if (item.set && item.set.setting) item.set.setting.display_time = 0;
        if (Array.isArray(item.creative) && item.creative[0]) {
          item.creative[0].start_time = 2240150400;
          item.creative[0].end_time = 2240150400;
        }
      }
    } else if (path.startsWith("/ws/faas/amap-navigation/main-page") && hasData) {
      if (data.cardList && typeof data.cardList === "object") {
        data.cardList = Object.values(data.cardList).filter(card => card && ["LoginCard", "FrequentLocation"].includes(card.dataType));
      }
      if (data.pull3 && Array.isArray(data.pull3.msgs)) data.pull3.msgs = [];
      for (const key of ["business_position", "mapBizList"]) if (key in data) data[key] = [];
    } else if (path.startsWith("/ws/shield/dsp/profile/index/nodefaas") && hasData) {
      delete data.tipData;
      const promoted = ["PopularActivitiesCardV5", "PopularActivitiesCardV2", "ContentCenterFeedCard"];
      if (data.cardList && typeof data.cardList === "object") {
        const cards = Array.isArray(data.cardList) ? data.cardList : Object.values(data.cardList);
        data.cardList = cards.filter(card => !card || typeof card !== "object" || !promoted.includes(card.dataKey));
      }
    } else if (path.startsWith("/ws/shield/search/new_hotword") && hasData) {
      if (Array.isArray(data.header_hotword)) data.header_hotword = [];
    } else if (path.startsWith("/ws/promotion-web/resource") && hasData) {
      for (const key of ["icon", "banner", "tips", "popup", "bubble", "other"]) {
        if (!(key in data)) continue;
        // Current taxi responses wrap banners and metadata together; keep that envelope.
        if (key === "banner" && data.banner && data.banner.data && Array.isArray(data.banner.data.banner)) {
          data.banner.data.banner = [];
        } else if (Array.isArray(data[key])) {
          data[key] = [];
        }
      }
    } else if (path.startsWith("/ws/msgbox/pull")) {
      if (Array.isArray(body.msgs)) body.msgs = [];
      if (body.pull3 && Array.isArray(body.pull3.msgs)) body.pull3.msgs = [];
    } else if (path.startsWith("/ws/message/notice/list") && hasData) {
      if (Array.isArray(data.noticeList)) data.noticeList = [];
    } else if (path.startsWith("/ws/shield/frogserver/aocs/updatable") && hasData) {
      const keywords = ["gd_notch_logo", "home_business_position_config", "his_input_tip", "operation_layer", "aiNative", "ai_", "_ai"];
      for (const key of Object.keys(data)) {
        if (keywords.some(keyword => key.includes(keyword))) data[key] = {status: 1, version: "", value: ""};
      }
    } else if (path.startsWith("/ws/shield/search/nearbyrec_smart") && hasData) {
      const promoted = ["coupon", "scene", "activity", "commodity_rec", "operation_activity"];
      for (const key of promoted) delete data[key];
      if (Array.isArray(data.modules)) data.modules = data.modules.filter(key => !promoted.includes(key));
    } else if (url[1] === "m5.amap.com" && path === "/ws/info_bff/ContentCenter" && body.page === "ContentCenter:29544" && hasData && data.modules) {
      const header = data.modules.header && data.modules.header.data;
      const waterfall = data.modules.waterfall && data.modules.waterfall.data;
      const titles = header && Array.isArray(header.list) ? header.list.map(item => item && item.title) : [];
      if (["关注", "推荐", "附近"].every(title => titles.includes(title)) && waterfall && Array.isArray(waterfall.list)) {
        // The user wants this entire social area hidden, including ordinary posts.
        header.list = [];
        waterfall.list = [];
        if (typeof waterfall.total === "number") waterfall.total = 0;
        if (typeof waterfall.hasMore === "string") waterfall.hasMore = "0";
        else if (typeof waterfall.hasMore === "boolean") waterfall.hasMore = false;
        else if (typeof waterfall.hasMore === "number") waterfall.hasMore = 0;
        if (data.regions && typeof data.regions === "object") {
          for (const key of Object.keys(data.regions)) {
            if (Array.isArray(data.regions[key])) data.regions[key] = data.regions[key].filter(module => !["header", "waterfall"].includes(module));
          }
        }
      }
    }
    const after = JSON.stringify(body);
    if (after !== before) result = {body: after};
  } catch (error) {
    console.log("[AMap] " + error.name + "; response unchanged");
  }
  $done(result);
})();
