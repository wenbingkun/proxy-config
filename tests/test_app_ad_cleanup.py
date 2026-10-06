from pathlib import Path
import json,subprocess
h=Path(__file__).resolve().parent.parent
profile='https://m5.amap.com/ws/shield/dsp/profile/index/nodefaasv3'
cases=[]
def add(name,url,body,expected=None):cases.append({'name':name,'url':url,'body':body,'expected':body if expected is None else expected})
normal=[{'dataKey':'MineNewVirtualAssetCardV3','content':{'ownedList':[{'id':1}]}},{'dataKey':'MineNewBEntranceCardV4','content':{'entranceList_1':[{'title':'收藏'}]}},{'dataKey':'MineNotifyCard','content':{'button':{'text':'通知'}}}]
for key in ['PopularActivitiesCardV5','PopularActivitiesCardV2','ContentCenterFeedCard']:
 add('promotion '+key,profile,{'data':{'cardList':normal+[{'dataKey':key,'content':{'item':[1]}}],'wallet':{'balance':12}}},{'data':{'cardList':normal,'wallet':{'balance':12}}})
add('all ordinary business cards preserved',profile,{'data':{'cardList':normal}})
add('unknown future card preserved',profile,{'data':{'cardList':[{'dataKey':'NewNormalCard'}]}})
add('optional malformed card preserved',profile,{'data':{'cardList':[None,1,{'dataKey':'MineNotifyCard'}]}})
for path in ['/ws/perception/drive/routePlan','/ws/shield/search_poi/search/sp','/ws/aos/locate']:
 add('navigation/search/location out of scope '+path,'https://m5.amap.com'+path,{'data':{'cardList':[{'dataKey':'PopularActivitiesCardV5'}],'route':{'distance':10}}})
add('unrelated domain unchanged','https://other.amap.com/ws/shield/dsp/profile/index/nodefaasv3',{'data':{'cardList':[{'dataKey':'PopularActivitiesCardV5'}]}})
add('shared content scope gate','https://m5.amap.com/ws/info_bff/ContentCenter',{'page':'poiReviews','data':{'modules':{'waterfall':{'data':{'list':[{'review':'normal'}]}}}}})
add('invalid JSON',profile,'<html>error</html>')
add('null body',profile,None)
content='https://m5.amap.com/ws/info_bff/ContentCenter'
add('home recommendation rollback',content,{'page':'ContentCenter:29544','data':{'modules':{'waterfall':{'data':{'list':[{'id':'normal recommendation'}]}}}}})
add('different numeric content page preserved',content,{'page':'ContentCenter:12345','data':{'modules':{'waterfall':{'data':{'list':[{'review':'normal'}]}}}}})
add('missing page preserved',content,{'data':{'modules':{'waterfall':{'data':{'list':[1]}}}}})
add('home malformed module preserved',content,{'page':'ContentCenter:29544','data':{'modules':{'waterfall':{'data':{'list':None}}}}})
js=r'''const vm=require('vm'),fs=require('fs');const src=fs.readFileSync(process.argv[1],'utf8');const cases=JSON.parse(process.argv[2]);const results=[];for(const c of cases){let calls=[];vm.runInNewContext(src,{$request:{url:c.url},$response:{body:typeof c.body==='string'?c.body:JSON.stringify(c.body)},console:{log(){}},$persistentStore:{read:()=>null,write:()=>true},$done:r=>calls.push(r)},{timeout:1000});if(calls.length!==1)throw Error(c.name+': completion');const got=calls[0].body===undefined?c.body:JSON.parse(calls[0].body);if(JSON.stringify(got)!==JSON.stringify(c.expected))throw Error(c.name+': output mismatch');results.push(c.name);}process.stdout.write(JSON.stringify(results));'''
r=subprocess.run(['node','-e',js,str(h/'quantumultx/scripts/amap-page-cleanup.js'),json.dumps(cases,ensure_ascii=False)],capture_output=True,text=True,check=True);print('AMap cleanup:',len(cases),'promotion/business/scope/boundary cases passed.')

for fixture in ['amap_home_cases.js', 'amap_taxi_cases.js']:
 subprocess.run(['node', str(h/'tests/fixtures'/fixture)], check=True)
# Cross-client rule scope and the jq expression must remain equivalent.
import re
sources = [h/'quantumultx/rewrite/StartupSupplement.conf', h/'loon/plugins/StartupSupplement.plugin', h/'surge/modules/converted/StartupSupplement.sgmodule']
patterns=[]
for path in sources:
 rules=[line.split(' ',1)[0] for line in path.read_text().splitlines() if line.startswith('^https')]
 assert len(rules)==13, path
 patterns.append(rules)
assert set(patterns[0])==set(patterns[1])==set(patterns[2])
sh,ehi,didi=[re.compile(next(p for p in patterns[0] if key in p)) for key in ['apiproxy', 'externalimage', 'img-ys011']]
assert sh.search('https://apiproxy.zuche.com/resource/cardes/toufang/marketing/v1?test=1')
for path in ['v11', 'v1/extra', 'v2']:
 assert not sh.search('https://apiproxy.zuche.com/resource/cardes/toufang/marketing/'+path)
for key in ['987dcb4241584cca926c2d34f5ff59db','499f25d69e14475a89e5436bd47f8293','3e3b519297244eeca14710366411df22','04c29fd6157947c882ceb04f311f64fe']:
 u='https://externalimage.1hai.cn/512/'+key+'.jpg'
 assert ehi.search(u) and ehi.search(u+'?cache=1')
 assert not ehi.search(u+'.extra')
 assert not ehi.search(u.replace('/512/','/1288/'))
assert not ehi.search('https://externalimage.1hai.cn/512/normal.jpg')
assert didi.search('https://img-ys011.didistatic.com/static/ad_oss/ad.jpg')
assert not didi.search('https://img-ys011.didistatic.com/static/normal/a.jpg')
exprs=[]
for path in ['quantumultx/rewrite/fmz200-Weibo.snippet','loon/plugins/Weibo.plugin','surge/modules/converted/Weibo.sgmodule']:
 line=next(l for l in (h/path).read_text().splitlines() if 'trends' in l and 'del(.data.banner' in l)
 exprs.append(line.split("'",2)[1])
assert len(set(exprs))==1
for obj in [{'data':{'banner':[1],'native_content':[1],'order':['hot','native_content','topics'],'hot':[2]}}, {'data':{'order':None,'hot':[2]}}, {'data':{'hot':[2]}}]:
 out=json.loads(subprocess.run(['jq','-c',exprs[0]],input=json.dumps(obj),text=True,capture_output=True,check=True).stdout)
 expected=json.loads(json.dumps(obj));expected['data'].pop('banner',None);expected['data'].pop('native_content',None)
 if isinstance(expected['data'].get('order'),list):expected['data']['order']=[x for x in expected['data']['order'] if x!='native_content']
 assert out==expected
assert '$persistentStore' not in (h/'quantumultx/scripts/amap-page-cleanup.js').read_text()
for path,names in [('quantumultx/bootstrap.example.conf',['AmapPageCleanup.conf','StartupSupplement.conf']),('loon/bootstrap.example.conf',['StartupSupplement.plugin'])]:
 for name in names:
  line=next(l for l in (h/path).read_text().splitlines() if name in l)
  assert line.endswith('enabled=false')
print('Three-client URL boundaries, normal-resource negatives, jq preservation and disabled defaults passed.')

script_url='https://raw.githubusercontent.com/wenbingkun/proxy-config/ced3ace1d4dfa6e6b1301dfb465cb6c5c4056bd8/quantumultx/scripts/amap-page-cleanup.js'
for path in ['quantumultx/rewrite/AmapPageCleanup.conf','loon/plugins/AmapPageCleanup.plugin','surge/modules/converted/AmapPageCleanup.sgmodule']:
 assert script_url in (h/path).read_text()

loon_template=(h/"loon/bootstrap.example.conf").read_text()
assert "https://kelee.one/Tool/Loon/Lpx/Amap_remove_ads.lpx," in loon_template
assert "loon/plugins/AmapPageCleanup.plugin," not in loon_template

# Restored promotions use one native jq rule per endpoint in each client.
for app, count in [('DaMai', 6), ('NeteaseMail', 1)]:
 paths=[h/f'quantumultx/rewrite/{app}.conf', h/f'loon/plugins/{app}.plugin', h/f'surge/modules/converted/{app}.sgmodule']
 rules=[]
 for path in paths:
  rows={}
  for line in path.read_text().splitlines():
   if "'" in line and ('jsonjq-response-body' in line or 'response-body-json-jq' in line or 'http-response-jq' in line):
    pattern=line.split()[1] if line.startswith('http-response-jq') else line.split()[0]
    assert pattern not in rows, (path,pattern)
    rows[pattern]=line.split("'",2)[1]
  assert len(rows)==count, (path,len(rows))
  rules.append(rows)
 assert rules[0]==rules[1]==rules[2], app
 for pattern, expr in rules[0].items():
  fixture={'data':{'orderList':[{'id':'order'}], 'ticketList':[1], 'balance':12,
                   'mailboxList':[{'id':'mail'}], 'quotaData':{'used':10},
                   'data':{'top':{'keywords':['ad'],'other':'business'}},
                   'dynamicMenu':{'itemList':[{'title':'订单'},{'title':'周边商城'}]},
                   'masterOperatorList':[1]}, 'status':0}
  out=json.loads(subprocess.run(['jq','-c',expr],input=json.dumps(fixture),text=True,capture_output=True,check=True).stdout)
  if 'home\\.float' not in pattern:
   for key in ['orderList','ticketList','balance','mailboxList','quotaData']:
    assert out['data'][key]==fixture['data'][key], (app,pattern,key)
  assert out['status']==0
  if 'page' in pattern and app=='NeteaseMail':
   assert 'masterOperatorList' not in out['data']
   for empty in [{}, {'data':None}, {'data':[]}]:
    got=json.loads(subprocess.run(['jq','-c',expr],input=json.dumps(empty),text=True,capture_output=True,check=True).stdout)
    assert got==empty
# Startup extraction must not reintroduce a dedicated App handler or the shared dispatcher.
for path in sources:
 text=path.read_text()
 assert 'amdc' not in '\n'.join(l for l in text.splitlines() if not l.startswith('#'))
 assert 'umetrip' not in text and 'damai' not in text and 'mailmaster' not in text
 assert 'script-path=' not in text and 'url script-' not in text
 for normal in ['https://api.m.jd.com/client.action?functionId=orderList',
                'https://api.yangkeduo.com/api/order/list',
                'https://acs.m.taobao.com/gw/mtop.alibaba.order.list/',
                'https://guide-acs.m.taobao.com/gw/mtop.taobao.order.list/',
                'https://app.dewu.com/api/v1/app/order/list',
                'https://yunbusiness.ccb.com/clp_service/txCtrl?txcode=ORDER']:
  assert not any(re.search(pattern,normal) for pattern in patterns[0]), normal
assert 'StartUpGaps.conf,' not in (h/'quantumultx/bootstrap.example.conf').read_text()
print('Restored native jq parity/business preservation and startup extraction negatives passed.')

for url in ['https://acs.m.taobao.com/gw/mtop.alibaba.advertisementservice.getadv/1.0/',
            'https://acs.m.taobao.com/gw/mtop.alibaba.cbu.app.homepage.startup/1.0/',
            'https://api.m.jd.com/client.action?functionId=start',
            'https://bdsp-x.jd.com/adx/start',
            'https://api.yangkeduo.com/api/cappuccino/splash',
            'https://guide-acs.m.taobao.com/gw/mtop.taobao.wireless.home.splash.awesome.get/1.0/',
            'https://app.dewu.com/api/v1/app/advertisement/start',
            'https://yunbusiness.ccb.com/clp_service/txCtrl?txcode=A3341A002',
            'https://res.xiaojukeji.com/resapi/activity/mget',
            'https://res.xiaojukeji.com/resapi/activity/getPreload']:
 assert any(re.search(p,url) for p in patterns[0]), url
for path in ['quantumultx/rewrite/Umetrip.conf','loon/plugins/Umetrip.plugin','surge/modules/converted/Umetrip.sgmodule']:
 text=(h/path).read_text()
 assert 'discardrp|startup' in text and 'discardrp.umetrip.com' in text
print('Ten extracted startup patterns and dedicated Umetrip startup ownership passed.')
