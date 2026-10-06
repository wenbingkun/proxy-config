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
 assert len(rules)==3, path
 patterns.append(rules)
assert patterns[0]==patterns[1]==patterns[2]
sh,ehi,didi=map(re.compile,patterns[0])
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
for path,names in [('quantumultx/bootstrap.example.conf',['Amap.snippet','StartupSupplement.conf']),('loon/bootstrap.example.conf',['StartupSupplement.plugin'])]:
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

assert "quantumultx/rewrite/AmapPageCleanup.conf," not in (h/"quantumultx/bootstrap.example.conf").read_text()
