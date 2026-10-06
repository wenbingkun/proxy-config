// 航旅纵横 response cleanup, maintained by proxy-config (2026-10-06).
// The endpoint/rpid/field definitions come from the reviewed umetrip.ads.js snapshot.
// This readable implementation keeps JSON and Protobuf paths; the old script stays unchanged
// for rollback. Unknown fields keep their original bytes. It does not unlock paid features.

const EMPTY_RPIDS = new Set(["1000019", "1420002", "1120000"]);
const EMPTY_PAYLOAD = new Uint8Array([10, 0, 16, 0, 32, 0]);
const MAX_DEPTH = 12;
const HOME_NAMES = ["机上闭门购虚拟卡片", "中秋国庆活动", "回归礼包_无行程首页底部条"];
const WATERFALL_NAMES = [
  "瀑布流_特价机票", "瀑布流_酒店", "瀑布流_租车卡片", "瀑布流_权益",
  "瀑布流_今日热议", "瀑布流_城市攻略", "瀑布流_景点攻略", "瀑布流_附近底部跳转",
];
const MINE_NAMES = [
  "我的页面-会员卡片V3", "我的页-腰部banner第四帧", "礼金中心", "票券权益", "特价专区-个人中心", "机票-31-推荐管",
];
const PROMOTION_STYLES = new Set(["1009", "1040", "1077", "3021"]);
const PROMOTION_MARKERS = [
  "/fs/advert/", '"serviceTrack":"advert_', '"advertiser"', '"advertId"',
  '"adBannerImg"', '"adTopImg"', '"midBannerImg"', '"inBannerImg"',
  '"impressionUrlList"', '"clickUrlList"', "/commonModal/", '"weexName":"commonModal"',
  "/payMember/", '"weexName":"payMember"', "/home-Equity/", "historyTripUnlock", "dutyfreeNew", "免税会员购",
];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseJson(text) {
  try { return {ok: true, value: JSON.parse(text)}; }
  catch (error) { return {ok: false}; }
}

function encodeUtf8(text) {
  const bytes = [];
  for (const character of text) {
    const point = character.codePointAt(0);
    if (point < 0x80) bytes.push(point);
    else if (point < 0x800) bytes.push(0xc0 | point >> 6, 0x80 | point & 63);
    else if (point < 0x10000) bytes.push(0xe0 | point >> 12, 0x80 | point >> 6 & 63, 0x80 | point & 63);
    else bytes.push(0xf0 | point >> 18, 0x80 | point >> 12 & 63, 0x80 | point >> 6 & 63, 0x80 | point & 63);
  }
  return new Uint8Array(bytes);
}

function decodeUtf8(bytes) {
  // Some client script engines do not expose TextDecoder.
  if (typeof TextDecoder !== "undefined") {
    try { return new TextDecoder("utf-8", {fatal: true}).decode(bytes); }
    catch (error) { return null; }
  }
  let text = "";
  for (let offset = 0; offset < bytes.length;) {
    const first = bytes[offset++];
    let point = first, following = 0, minimum = 0;
    if (first >= 0xc2 && first <= 0xdf) { point &= 31; following = 1; minimum = 0x80; }
    else if (first >= 0xe0 && first <= 0xef) { point &= 15; following = 2; minimum = 0x800; }
    else if (first >= 0xf0 && first <= 0xf4) { point &= 7; following = 3; minimum = 0x10000; }
    else if (first >= 0x80) return null;
    if (offset + following > bytes.length) return null;
    for (let index = 0; index < following; index++) {
      const next = bytes[offset++];
      if ((next & 0xc0) !== 0x80) return null;
      point = point * 64 + (next & 63);
    }
    if (point < minimum || point > 0x10ffff || point >= 0xd800 && point <= 0xdfff) return null;
    text += String.fromCodePoint(point);
  }
  return text;
}

function contains(bytes, text) {
  const needle = encodeUtf8(text);
  outer: for (let offset = 0; offset <= bytes.length - needle.length; offset++) {
    for (let index = 0; index < needle.length; index++) {
      if (bytes[offset + index] !== needle[index]) continue outer;
    }
    return true;
  }
  return false;
}

function readVarint(bytes, offset) {
  let value = 0;
  for (let shift = 0; shift <= 49 && offset < bytes.length; shift += 7) {
    const byte = bytes[offset++];
    value += (byte & 127) * Math.pow(2, shift);
    if (!Number.isSafeInteger(value)) return null;
    if (!(byte & 128)) return {value, offset};
  }
  return null;
}

function skipVarint(bytes, offset) {
  // Preserve 64-bit business values without converting them to unsafe JS numbers.
  for (let index = 0; index < 10 && offset < bytes.length; index++) {
    const byte = bytes[offset++];
    if (index === 9 && byte > 1) return null;
    if (!(byte & 128)) return offset;
  }
  return null;
}

function encodeVarint(value) {
  const bytes = [];
  while (value >= 128) {
    bytes.push(value % 128 | 128);
    value = Math.floor(value / 128);
  }
  bytes.push(value);
  return new Uint8Array(bytes);
}

function concat(parts) {
  const bytes = new Uint8Array(parts.reduce((length, part) => length + part.length, 0));
  let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.length; }
  return bytes;
}

function parseMessage(bytes) {
  const fields = [];
  for (let offset = 0; offset < bytes.length;) {
    const start = offset, tag = readVarint(bytes, offset);
    if (!tag) return null;
    offset = tag.offset;
    const number = Math.floor(tag.value / 8), wire = tag.value % 8;
    if (number < 1 || number > 0x1fffffff || ![0, 1, 2, 5].includes(wire)) return null;
    let data;
    if (wire === 0) {
      const end = skipVarint(bytes, offset);
      if (end === null) return null;
      offset = end;
    } else if (wire === 2) {
      const length = readVarint(bytes, offset);
      if (!length || length.value > bytes.length - length.offset) return null;
      offset = length.offset;
      data = bytes.slice(offset, offset + length.value);
      offset += length.value;
    } else {
      offset += wire === 1 ? 8 : 4;
      if (offset > bytes.length) return null;
    }
    fields.push({number, wire, data, raw: bytes.slice(start, offset)});
  }
  return fields;
}

function encodeMessage(fields) {
  return concat(fields.map(field => field.dirty
    ? concat([encodeVarint(field.number * 8 + 2), encodeVarint(field.data.length), field.data])
    : field.raw));
}

function fieldText(fields, number) {
  if (!fields) return null;
  const field = fields.find(field => field.number === number && field.wire === 2);
  return field ? decodeUtf8(field.data) : null;
}

function cardMatches(fields, number, predicate) {
  if (!fields) return false;
  return fields.some(field => {
    if (field.number !== 8 || field.wire !== 2) return false;
    const children = parseMessage(field.data);
    return !!children && children.some(child => child.number === number && child.wire === 2 && predicate(child.data));
  });
}

function cardNameMatches(fields, names) {
  return cardMatches(fields, 3, bytes => names.includes(decodeUtf8(bytes)));
}

function cardJsonMatches(fields, predicate) {
  return cardMatches(fields, 7, bytes => {
    const text = decodeUtf8(bytes);
    if (!text || !["{", "["].includes(text[0])) return false;
    const parsed = parseJson(text);
    return parsed.ok && predicate(parsed.value);
  });
}

function removeNodes(bytes, predicate, depth = 0) {
  if (depth > MAX_DEPTH) return {bytes, count: 0};
  const fields = parseMessage(bytes);
  if (!fields) return {bytes, count: 0};
  const kept = [];
  let count = 0;
  for (const field of fields) {
    if (field.wire === 2) {
      const children = parseMessage(field.data);
      if (predicate(field.number, field.data, children)) { count++; continue; }
      if (children) {
        const result = removeNodes(field.data, predicate, depth + 1);
        if (result.count) { field.data = result.bytes; field.dirty = true; count += result.count; }
      }
    }
    kept.push(field);
  }
  return {bytes: count ? encodeMessage(kept) : bytes, count};
}

function cleanHomeObject(value) {
  if (!value || typeof value !== "object") return false;
  let changed = false;
  if (Array.isArray(value.groupList)) {
    const oldLength = value.groupList.length;
    value.groupList = value.groupList.filter(group => !isObject(group) || !(
      String(group.dataSource || "").toUpperCase() === "ADVERT" || Number(group.groupStyle) === 1009 ||
      String(group.groupTitle || group.title || "") === "中秋国庆活动"));
    changed = value.groupList.length !== oldLength || changed;
  }
  if (Array.isArray(value.cardList)) {
    const oldLength = value.cardList.length;
    value.cardList = value.cardList.filter(card => !isObject(card) || !(
      /^advert_/i.test(String(card.serviceTrack || "")) || /\/fs\/advert\//i.test(JSON.stringify(card)) ||
      /"advertId"\s*:/i.test(JSON.stringify(card)) || String(card.serviceName || "").includes("广告位-") ||
      String(card.serviceName || "").includes("娱乐运营活动")));
    changed = value.cardList.length !== oldLength || changed;
  }
  for (const child of Object.values(value)) changed = cleanHomeObject(child) || changed;
  return changed;
}

function rewriteEmbeddedJson(bytes, depth = 0) {
  if (depth > MAX_DEPTH) return {bytes, count: 0};
  const fields = parseMessage(bytes);
  if (!fields) return {bytes, count: 0};
  let count = 0;
  for (const field of fields) {
    if (field.wire !== 2) continue;
    const text = [123, 91].includes(field.data[0]) ? decodeUtf8(field.data) : null;
    const parsed = text === null ? {ok: false} : parseJson(text);
    if (parsed.ok && cleanHomeObject(parsed.value)) {
      field.data = encodeUtf8(JSON.stringify(parsed.value)); field.dirty = true; count++;
    } else if (parseMessage(field.data)) {
      const result = rewriteEmbeddedJson(field.data, depth + 1);
      if (result.count) { field.data = result.bytes; field.dirty = true; count += result.count; }
    }
  }
  return {bytes: count ? encodeMessage(fields) : bytes, count};
}

function cleanFamily(bytes, depth = 0) {
  if (depth > MAX_DEPTH) return {bytes, count: 0};
  const fields = parseMessage(bytes);
  if (!fields) return {bytes, count: 0};
  const isTrial = field => field.number === 1 && field.wire === 2 && contains(field.data, "可免费试用30天") && contains(field.data, "payMember");
  const isGuard = field => field.number === 2 && field.wire === 2 && contains(field.data, "添加家人并开启守护") && contains(field.data, "payMember");
  if (fields.some(isTrial) && fields.some(isGuard)) {
    const kept = fields.filter(field => !isTrial(field) && !isGuard(field));
    return {bytes: encodeMessage(kept), count: fields.length - kept.length};
  }
  let count = 0;
  for (const field of fields) {
    if (field.wire !== 2 || !parseMessage(field.data)) continue;
    const result = cleanFamily(field.data, depth + 1);
    if (result.count) { field.data = result.bytes; field.dirty = true; count += result.count; }
  }
  return {bytes: count ? encodeMessage(fields) : bytes, count};
}

function cleanPayload(bytes, rpid) {
  switch (rpid) {
    case "1000002": {
      const removed = removeNodes(bytes, (number, data, fields) => number === 5 && !!fields && (
        fields.some(field => field.number === 37 && field.wire === 2 && decodeUtf8(field.data) === "ADVERT") || cardNameMatches(fields, HOME_NAMES) ||
        cardJsonMatches(fields, value => value && Number(value.groupId) === 111450)));
      const rewritten = rewriteEmbeddedJson(removed.bytes);
      return {bytes: rewritten.bytes, count: removed.count + rewritten.count};
    }
    case "1000029": return removeNodes(bytes, (number, data) => number === 8 && WATERFALL_NAMES.some(name => contains(data, name)));
    case "1370126": return removeNodes(bytes, (number, data) => number === 8 && ["付费会员", "更早历史行程待解锁"].some(name => contains(data, name)));
    case "1370279": return cleanFamily(bytes);
    case "1011058": return removeNodes(bytes, (number, data) => number === 11 && contains(data, "历史行程容量剩余") && contains(data, "付费会员"));
    case "1060060": return removeNodes(bytes, (number, data) => number === 12 && contains(data, "付费会员"));
    case "1100001": return removeNodes(bytes, (number, data, fields) => number === 5 && (
      cardNameMatches(fields, MINE_NAMES) || cardJsonMatches(fields, value => value && (
        [111402, 111403, 111404].includes(Number(value.groupId)) ||
        String(isObject(value.trackParam) ? value.trackParam.department || "" : "").toLowerCase() === "advert"))));
    default: return {bytes, count: 0};
  }
}

function cleanProtobuf(bytes) {
  const fields = parseMessage(bytes);
  if (!fields) return {bytes, count: 0};
  const headers = typeof $request !== "undefined" && $request.headers || {};
  const key = Object.keys(headers).find(key => key.toLowerCase() === "rpid");
  const rpid = (key === undefined ? null : String(headers[key])) || fieldText(fields, 5);
  let count = 0;
  for (const field of fields) {
    if (field.number !== 7 || field.wire !== 2) continue;
    const result = EMPTY_RPIDS.has(rpid) ? {bytes: EMPTY_PAYLOAD, count: 1} : cleanPayload(field.data, rpid);
    if (result.count) { field.data = result.bytes; field.dirty = true; count += result.count; }
  }
  return {bytes: count ? encodeMessage(fields) : bytes, count};
}

function isPromotionCard(card) {
  if (!isObject(card)) return false;
  const payload = typeof card.cardResult === "string" ? card.cardResult : isObject(card.cardResult) ? JSON.stringify(card.cardResult) : "";
  if (PROMOTION_MARKERS.some(marker => payload.includes(marker)) || payload.includes("memberCardbag") && payload.includes("buyPage")) return true;
  const parsed = typeof card.cardResult === "string" ? parseJson(card.cardResult) : {ok: true, value: card.cardResult};
  const value = parsed.ok && parsed.value;
  return isObject(value) && (/^advert_/i.test(value.serviceTrack || "") || value.advertId !== undefined || value.advertiser !== undefined);
}

function cleanGroups(groups) {
  if (!Array.isArray(groups)) return groups;
  const kept = [];
  for (const group of groups) {
    if (!isObject(group)) { kept.push(group); continue; }
    if (PROMOTION_STYLES.has(String(group.groupStyle)) || String(group.dataSource || "").toUpperCase() === "ADVERT" ||
        typeof group.businessType === "string" && /(?:advert|marketing|promotion|paymember|member|vip)/i.test(group.businessType)) continue;
    if (Array.isArray(group.subGroupList)) group.subGroupList = cleanGroups(group.subGroupList);
    if (Array.isArray(group.cardList)) {
      const previous = group.cardList.length;
      group.cardList = group.cardList.filter(card => !isPromotionCard(card));
      if (previous > 0 && group.cardList.length === 0 && (!Array.isArray(group.subGroupList) || group.subGroupList.length === 0)) continue;
    }
    kept.push(group);
  }
  return kept;
}

function cleanJsonBody(body) {
  if (!isObject(body)) return;
  if (Array.isArray(body.advertPageInfo) || Object.prototype.hasOwnProperty.call(body, "advertTotalTimeout") ||
      Object.prototype.hasOwnProperty.call(body, "warmStartInterval")) {
    if (Array.isArray(body.advertPageInfo)) body.advertPageInfo = [];
    if (isObject(body.page) && Array.isArray(body.page.groupList)) body.page.groupList = [];
    if (Array.isArray(body.pageIdBlackList)) body.pageIdBlackList = [];
    if (typeof body.advertTotalTimeout === "number") body.advertTotalTimeout = 0;
  }
  for (const container of [body.templateService, body.page]) {
    if (isObject(container) && Array.isArray(container.groupList)) container.groupList = cleanGroups(container.groupList);
  }
  if (Object.prototype.hasOwnProperty.call(body, "isShowPop") && isObject(body.page)) body.isShowPop = 0;
  if (Array.isArray(body.advertDataList)) body.advertDataList = [];
  if (Array.isArray(body.cardCollections) && Object.prototype.hasOwnProperty.call(body, "isLastPage") &&
      Object.prototype.hasOwnProperty.call(body, "lastParam")) {
    body.cardCollections = []; body.isLastPage = true; body.lastParam = "";
  }
  if (isObject(body.historyTripCapacity) && Object.prototype.hasOwnProperty.call(body.historyTripCapacity, "showTripCapacityBar")) body.historyTripCapacity.showTripCapacityBar = 0;
  if (Array.isArray(body.vipShowInfoList)) body.vipShowInfoList = [];
}

function transformJson(text) {
  const parsed = parseJson(text);
  if (!parsed.ok) return null;
  const document = parsed.value, bodies = [];
  if (isObject(document)) {
    bodies.push(document);
    if (isObject(document.s2cRspBodyWrapPB)) bodies.push(document.s2cRspBodyWrapPB.responsebody, document.s2cRspBodyWrapPB.responseBody);
    if (isObject(document.presp)) bodies.push(document.presp.pdata);
    bodies.push(document.responsebody, document.responseBody);
  }
  for (const body of bodies) cleanJsonBody(body);
  return JSON.stringify(document);
}

(function run() {
  let result = {};
  try {
    if (typeof $response !== "undefined" && $response.bodyBytes) {
      const bytes = new Uint8Array($response.bodyBytes), text = decodeUtf8(bytes);
      const json = text === null ? null : transformJson(text);
      if (json !== null) result = {bodyBytes: encodeUtf8(json).buffer};
      else {
        const cleaned = cleanProtobuf(bytes);
        if (cleaned.count) result = {bodyBytes: cleaned.bytes.buffer.slice(cleaned.bytes.byteOffset, cleaned.bytes.byteOffset + cleaned.bytes.byteLength)};
        else console.log("[Umetrip] No recognized Protobuf cleanup; response unchanged");
      }
    } else if (typeof $response !== "undefined" && typeof $response.body === "string") {
      const json = transformJson($response.body);
      if (json !== null) result = {body: json};
      else console.log("[Umetrip] Invalid JSON response; response unchanged");
    }
  } catch (error) {
    console.log("[Umetrip] Cleanup failed; response unchanged: " + String(error));
    result = {};
  }
  $done(result);
})();
