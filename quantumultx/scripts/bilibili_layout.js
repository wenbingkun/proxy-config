/*
 * Bilibili layout cleanup for Quantumult X.
 *
 * Complements the Biliverse ADBlock snippet, which does not touch the tab bar
 * or the "我的" page:
 *   - x/resource/show/tab(/v2): bottom bar -> 首页 / 动态 / 我的,
 *     top tabs -> 直播 / 推荐 / 热门 / 番剧(动画) / 影视, top-right -> 消息 only.
 *   - x/v2/account/mine(/ipad): drop 会员购, 游戏中心, 创作中心 and other
 *     promotional entries plus the VIP banners.
 *
 * It only filters what the server sends back; nothing is invented. If a filter
 * would leave a list empty (API change), that list is left untouched, and any
 * error passes the original response through.
 */

const BOTTOM_KEEP = ["bilibili://main/home", "bilibili://following/home", "bilibili://user_center"];
const TAB_KEEP = [
  "bilibili://live/home",
  "bilibili://pegasus/promo",
  "bilibili://pegasus/hottopic",
  "bilibili://pgc/bangumi",
  "bilibili://pgc/cinema",
];
const TOP_KEEP = ["bilibili://link/im_home"];

const MINE_DROP_TITLES = [
  "会员购",
  "游戏中心",
  "我的课程",
  "免流量服务",
  "能量加油站",
  "工房集市",
  "漫画",
  "必火推广",
  "时光照相馆",
  "社区中心",
];
const MINE_DROP_URIS = [
  "bilibili://mall/home",
  "game_center",
  "bilibili://cheese",
  "freedata",
  "bilibili://comic",
];
const MINE_DROP_SECTIONS = ["创作中心"];
const MINE_DROP_FIELDS = ["vip_section", "vip_section_v2", "modular_vip_section", "live_tip", "answer"];
const MINE_LISTS = ["ipad_sections", "ipad_upper_sections", "ipad_recommend_sections", "ipad_more_sections"];

function keepByUri(list, prefixes) {
  if (!Array.isArray(list)) return list;
  const kept = list.filter((item) => item && typeof item.uri === "string" && prefixes.some((p) => item.uri.startsWith(p)));
  return kept.length ? kept : list;
}

function isPromo(item) {
  if (!item) return false;
  const title = item.title || "";
  const uri = item.uri || "";
  return MINE_DROP_TITLES.includes(title) || MINE_DROP_URIS.some((u) => uri.includes(u));
}

function cleanLayout(data) {
  data.bottom = keepByUri(data.bottom, BOTTOM_KEEP);
  data.tab = keepByUri(data.tab, TAB_KEEP);
  data.top = keepByUri(data.top, TOP_KEEP);
}

function cleanMine(data) {
  MINE_DROP_FIELDS.forEach((key) => delete data[key]);

  if (Array.isArray(data.sections_v2)) {
    data.sections_v2 = data.sections_v2
      .filter((section) => section && !MINE_DROP_SECTIONS.includes(section.title) && !MINE_DROP_SECTIONS.includes(section.up_title))
      .map((section) => {
        if (Array.isArray(section.items)) section.items = section.items.filter((item) => !isPromo(item));
        return section;
      })
      .filter((section) => !Array.isArray(section.items) || section.items.length);
  }

  MINE_LISTS.forEach((key) => {
    if (Array.isArray(data[key])) data[key] = data[key].filter((item) => !isPromo(item));
  });
}

function main(url, body) {
  const obj = JSON.parse(body);
  if (!obj || !obj.data) return body;
  if (/\/x\/resource\/show\/tab/.test(url)) cleanLayout(obj.data);
  else if (/\/x\/v2\/account\/mine/.test(url)) cleanMine(obj.data);
  else return body;
  return JSON.stringify(obj);
}

if (typeof $done !== "undefined") {
  try {
    $done({ body: main($request.url, $response.body) });
  } catch (e) {
    console.log("bilibili_layout: " + e);
    $done({});
  }
} else if (typeof module !== "undefined") {
  module.exports = { main };
}
