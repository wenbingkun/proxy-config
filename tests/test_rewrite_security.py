#!/usr/bin/env python3
"""Run security revisions with actual client result shapes and adversarial payloads."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://raw.githubusercontent.com/wenbingkun/proxy-config/main/quantumultx/scripts/'
HARNESS = r'''
const fs = require('fs'), vm = require('vm');
const cases=JSON.parse(process.argv[1]), result=[];
for(const c of cases){
 let calls=[];
 const response={body:c.body};
 if(c.bytes){
  const b=Buffer.from(c.body);
  if(c.client==='qx') response.bodyBytes=Uint8Array.from(b).buffer;
  else response.body=Uint8Array.from(b);
 }
 const ctx={$request:{url:c.url},$response:response,$done:x=>calls.push(x),console:{log:()=>{}},Uint8Array,ArrayBuffer,TextDecoder};
 if(c.client==='qx')ctx.$task={}; else ctx.$httpClient={};
 if(c.client==='loon')ctx.$loon={};
 vm.runInNewContext(fs.readFileSync('quantumultx/scripts/'+c.file,'utf8'),ctx);
 result.push({calls:calls.length,value:calls[0]});
}
console.log(JSON.stringify(result));
'''


def main():
    cases, expected = [], []
    def add(file, client, url, body, want, **kwargs):
        cases.append(dict(file=file, client=client, url=url, body=body, **kwargs)); expected.append(want)
    for client in ('qx', 'surge'):
        for target in ('https://example.com/a%2Fb?q=x%2By', 'http://example.com/a',
                       'https%3A%2F%2Fexample.com%2Fa%3Fx%3D1%26y%3D2', 'HTTPS://example.com/a'):
            wanted = target if '://' in target else 'https://example.com/a?x=1&y=2'
            response = {'status': 'HTTP/1.1 302 Found', 'headers': {'Location': wanted, 'Cache-Control': 'no-store'}, 'body': ''} if client == 'qx' else {'response': {'status': 302, 'headers': {'Location': wanted, 'Cache-Control': 'no-store'}, 'body': ''}}
            add('zhihu-redirect.js', client, 'https://link.zhihu.com/?target='+target+'&source=search', '', response)
        for bad in ('javascript%3Aalert(1)', 'https%3A%2F%2Fexample.com%0D%0AX-Key%3Ax', '%zz',
                    'https://user@example.com/', 'https://example.com\\evil', 'https://', '', 'https%253A%252F%252Fexample.com',
                    'https://example.com&target=https://other.test'):
            response = {'status': 'HTTP/1.1 400 Bad Request', 'headers': {'Content-Type':'text/plain','Cache-Control':'no-store'}, 'body':'Invalid external-link target'} if client=='qx' else {'response':{'status':400,'headers':{'Content-Type':'text/plain','Cache-Control':'no-store'},'body':'Invalid external-link target'}}
            add('zhihu-redirect.js', client, 'https://link.zhihu.com/?target='+bad, '', response)
        add('zhihu-redirect.js', client, 'https://other.test/?target=https://example.com', '', {})
        endpoint = 'https://api.zhihu.com/search/recommend_query/v2?x=1'
        original = {'recommend_queries': {'items':[{'text': 'quoted } " value'}]}, 'other':{'keep':1}}
        add('zhihu-recommend.js',client,endpoint,json.dumps(original),{'json':{'recommend_queries':{},'other':{'keep':1}}})
        for body in ('{"recommend_queries":{},"other":1}', '{bad', '{"recommend_queries":{"x":1},"id":9007199254740993}'):
            add('zhihu-recommend.js',client,endpoint,body,{})
        add('zhihu-recommend.js',client,'https://other.test/',json.dumps(original),{})
    for client in ('qx','surge','loon'):
        for bytes_mode in (False,True):
            for body in (' { "id":9007199254740993 } ', '{"advertDataList":[],"id":9007199254740993}',
                         '{"advertDataList":[{"ad":1}],"id":9007199254740993}'):
                add('umetrip-safe.js',client,'https://home.umetrip.com/gateway/api/umetrip/native',body,{},bytes=bytes_mode)
    for url in ('https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/dynamic_history?x=1',
                'https://unknown.test/'):
        add('bilibili_json-safe.js','qx',url,'{"id":9007199254740993}',{'body':'{"id":9007199254740993}'})
    got=json.loads(subprocess.run(['node','-e',HARNESS,json.dumps(cases)],cwd=ROOT,capture_output=True,text=True,check=True).stdout)
    for case, result, want in zip(cases,got,expected):
        assert result['calls']==1,(case,result)
        value=result['value']
        if 'json' in want: assert json.loads(value['body'])==want['json'],(case,result)
        else: assert value==want,(case,result,want)
    # Bind the simulated scripts to the published container rules and reject the old unsafe rules.
    qx=(ROOT/'quantumultx/rewrite/fmz200-Zhihu.snippet').read_text()
    surge=(ROOT/'surge/modules/rewrite/zhihu.sgmodule').read_text()
    assert 'http://$4' not in qx+surge and 'recommend_queries":\\{' not in qx
    assert 'url script-echo-response '+BASE+'zhihu-redirect.js' in qx
    assert 'url script-response-body '+BASE+'zhihu-recommend.js' in qx
    assert 'zhihu_redirect = type=http-request' in surge and 'zhihu_recommend = type=http-response' in surge
    assert 'dynamic_svr' not in '\n'.join(l for l in (ROOT/'quantumultx/rewrite/bilibili_ad.conf').read_text().splitlines() if not l.startswith('#'))
    print(f'Rewrite security: {len(cases)} client/payload cases passed.')


if __name__ == '__main__':
    main()
