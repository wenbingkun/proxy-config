// Observed 2026-10-06 schemas; preserve navigation, search and unknown components.
(function run() {
  const CONTENT_PAGE = "ContentCenter:29544"; // Home social-feed scope observed on this device.
  let result = {};
  try {
    if (/^https:\/\/m5-zb\.amap\.com\/ws\/promotion-web\/resource\/home(?:\?|$)/.test(String($request.url || ""))) {
      const body = JSON.parse($response.body);
      const banners = body && body.data && body.data.banner && body.data.banner.data;
      if (banners && Array.isArray(banners.banner)) {
        banners.banner = [];
        result = {body:JSON.stringify(body)};
      }
    }

    const url = String($request.url || ""), match = url.match(/^https:\/\/m5\.amap\.com(\/[^?#]*)(?:\?[^#]*)?(?:#.*)?$/);
    const profile = "/ws/shield/dsp/profile/index/nodefaasv3", content = "/ws/info_bff/ContentCenter";
    if (match && [profile,content].includes(match[1]) && typeof $response.body === "string") {
      const body = JSON.parse($response.body), before = JSON.stringify(body), data = body && body.data;
      if (data && typeof data === "object" && !Array.isArray(data)) {
        if (match[1] === profile && Array.isArray(data.cardList)) {
          const promoted = ["PopularActivitiesCardV5", "PopularActivitiesCardV2", "ContentCenterFeedCard"];
          data.cardList = data.cardList.filter(card => !card || typeof card !== "object" || !promoted.includes(card.dataKey));
        } else if (match[1] === content && body.page === CONTENT_PAGE && data.modules) {
          const header = data.modules.header && data.modules.header.data;
          const waterfall = data.modules.waterfall && data.modules.waterfall.data;
          const titles = header && Array.isArray(header.list) ? header.list.map(item => item && item.title) : [];
          const home = ["关注", "推荐", "附近"].every(title => titles.includes(title));
          if (home && waterfall && Array.isArray(waterfall.list)) {
            // The user explicitly wants the entire social-content area hidden, including normal posts.
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
      }
      const after = JSON.stringify(body);
      if (after !== before) result = {body:after};
    }
  } catch(error) { console.log("[AMap page cleanup] " + error.name + "; response unchanged"); }
  $done(result);
})();
