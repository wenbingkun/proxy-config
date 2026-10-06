#!/usr/bin/env python3
"""Exercise the combined AMap response handler and its actual QX/Surge entry points."""
from pathlib import Path
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
cases = []


def add(path, body, expected=None, host="m5.amap.com"):
    cases.append({"url": "https://" + host + path, "body": body,
                  "expected": body if expected is None else expected})


add("/ws/faas/amap-navigation/main-page", {
    "data": {"cardList": [{"dataType": "LoginCard"}, {"dataType": "FrequentLocation"}, {"dataType": "Promotion"}],
             "business_position": [1], "mapBizList": [2], "pull3": {"msgs": [3], "keep": 4}, "route": {"id": 5}}}, {
    "data": {"cardList": [{"dataType": "LoginCard"}, {"dataType": "FrequentLocation"}],
             "business_position": [], "mapBizList": [], "pull3": {"msgs": [], "keep": 4}, "route": {"id": 5}}})
profile = "/ws/shield/dsp/profile/index/nodefaasv3"
normal = [{"dataKey": "MineNewVirtualAssetCardV3", "wallet": 12}, {"dataKey": "MineNewBEntranceCardV4", "title": "收藏"},
          {"dataType": "MyOrderCard"}, {"dataType": "GdRecommendCard"}, {"dataKey": "UnknownFutureBusinessCard"}]
for key in ["PopularActivitiesCardV5", "PopularActivitiesCardV2", "ContentCenterFeedCard"]:
    add(profile, {"data": {"tipData": {"ad": 1}, "cardList": normal + [{"dataKey": key}], "orders": [1]}},
        {"data": {"cardList": normal, "orders": [1]}})
add(profile, {"data": {"cardList": normal}})
add("/ws/promotion-web/resource/home", {"data": {"banner": {"data": {"banner": [1], "callback": "keep"}},
    "popup": [2], "other": {"data": {"normal": 3}}, "order": {"id": 4}}},
    {"data": {"banner": {"data": {"banner": [], "callback": "keep"}},
    "popup": [], "other": {"data": {"normal": 3}}, "order": {"id": 4}}}, host="m5-zb.amap.com")
add("/ws/promotion-web/resource", {"data": {"banner": [1], "icon": [2], "tips": [3], "order": [4]}},
    {"data": {"banner": [], "icon": [], "tips": [], "order": [4]}}, host="m5-zb.amap.com")
add("/ws/shield/search/new_hotword", {"data": {"header_hotword": [1], "search": "keep"}},
    {"data": {"header_hotword": [], "search": "keep"}})
add("/ws/msgbox/pull", {"msgs": [1], "pull3": {"msgs": [2], "keep": 3}, "other": [4]},
    {"msgs": [], "pull3": {"msgs": [], "keep": 3}, "other": [4]})
add("/ws/message/notice/list", {"data": {"noticeList": [1], "keep": 2}}, {"data": {"noticeList": [], "keep": 2}})
add("/ws/shield/search/nearbyrec_smart", {"data": {"coupon": [1], "commodity_rec": [2], "modules": ["coupon", "nearby"], "nearby": [3]}},
    {"data": {"modules": ["nearby"], "nearby": [3]}})
add("/ws/shield/frogserver/aocs/updatable/1", {"data": {"home_business_position_config": {"value": "ad"}, "navigation": {"value": "keep"}}},
    {"data": {"home_business_position_config": {"status": 1, "version": "", "value": ""}, "navigation": {"value": "keep"}}})
add("/ws/valueadded/alimama/splash_screen", {"data": {"ad": [{"set": {"setting": {"display_time": 3}}, "creative": [{"start_time": 1, "end_time": 2}]}]}},
    {"data": {"ad": [{"set": {"setting": {"display_time": 0}}, "creative": [{"start_time": 2240150400, "end_time": 2240150400}]}]}})
content = "/ws/info_bff/ContentCenter"
home = {"page": "ContentCenter:29544", "data": {"modules": {
    "header": {"data": {"list": [{"title": title} for title in ["关注", "推荐", "附近"]]}},
    "waterfall": {"data": {"list": [1], "total": 1, "hasMore": "1"}}, "other": {"data": {"keep": 1}}},
    "regions": {"content": ["header", "other", "waterfall"]}}}
expected = json.loads(json.dumps(home))
expected["data"]["modules"]["header"]["data"]["list"] = []
expected["data"]["modules"]["waterfall"]["data"] = {"list": [], "total": 0, "hasMore": "0"}
expected["data"]["regions"]["content"] = ["other"]
add(content, home, expected)
other_page = json.loads(json.dumps(home)); other_page["page"] = "ContentCenter:99999"
add(content, other_page)
add(content, home, host="other.amap.com")
for path in ["/ws/perception/drive/routePlan", "/ws/shield/search_poi/search/sp", "/ws/aos/locate", "/ws/boss/order/car/configInfo"]:
    add(path, {"data": {"cardList": [{"dataKey": "PopularActivitiesCardV5"}], "route": 1}})
add(profile, {"data": {"tipData": [1]}}, host="amap.com.evil.example")
for body in [None, [], {"data": None}, {"data": {"cardList": None}}, "<html>error</html>"]:
    add(profile, body)

harness = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(process.argv[1],'utf8'),cases=JSON.parse(process.argv[2]);
for(const client of ['QX','Surge']) for(const c of cases){
 const calls=[];const ctx={$request:{url:c.url},$response:{body:typeof c.body==='string'?c.body:JSON.stringify(c.body)},
  $done:v=>calls.push(v),console:{log(){}},...(client==='QX'?{$task:{}}:{$httpClient:{}})};
 vm.runInNewContext(source,ctx,{timeout:1000});assert.equal(calls.length,1,c.url);
 const got=calls[0].body===undefined?c.body:JSON.parse(calls[0].body);
 assert.deepStrictEqual(got,c.expected,client+' '+c.url);
}
"""
subprocess.run(["node", "-e", harness, str(ROOT/"quantumultx/scripts/amap.js"), json.dumps(cases, ensure_ascii=False)], check=True)

qx = (ROOT/"quantumultx/rewrite/Amap.snippet").read_text()
surge = (ROOT/"surge/modules/converted/Amap.sgmodule").read_text()
qx_scripts = [re.compile(l.split()[0]) for l in qx.splitlines() if " url script-response-body " in l]
surge_scripts = [re.compile(re.search(r"pattern=(\S+), script-path=", l)[1]) for l in surge.splitlines() if "type=http-response" in l]
assert {p.pattern for p in qx_scripts} == {p.pattern for p in surge_scripts}
for path, host in [(profile,"m5.amap.com"), ("/ws/promotion-web/resource/home","m5-zb.amap.com"), (content,"m5.amap.com")]:
    for patterns in [qx_scripts, surge_scripts]:
        assert sum(bool(p.search("https://"+host+path+"?test=1")) for p in patterns) == 1
for path in ["/ws/perception/drive/routePlan", "/ws/shield/search_poi/search/sp", "/ws/aos/locate", "/ws/boss/order/car/configInfo"]:
    assert not any(p.search("https://m5.amap.com"+path) for p in qx_scripts+surge_scripts)
assert "amdc" not in "\n".join(l for l in qx.splitlines() if not l.startswith(("#", ";")))
assert "amdc" not in "\n".join(l for l in surge.splitlines() if not l.startswith("#"))
qx_filters = [l for l in qx.splitlines() if l.startswith(("host,", "host-suffix,"))]
surge_filters = [l for l in surge.splitlines() if l.startswith(("DOMAIN,", "DOMAIN-SUFFIX,"))]
converted = [l.replace("host-suffix, ", "DOMAIN-SUFFIX,").replace("host, ", "DOMAIN,").replace(", reject", ",REJECT") for l in qx_filters]
assert converted == surge_filters
assert len(qx_filters) == 14 and all(l.endswith("reject") for l in qx_filters)
print(f"Combined AMap: {len(cases)*2} response cases, unique matching scripts and preserved 14 domain filters passed.")
