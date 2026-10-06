const fs=require('fs'),vm=require('vm'),assert=require('assert');const src=fs.readFileSync(__dirname+'/../../quantumultx/scripts/amap-page-cleanup.js','utf8');
function run(url,body){let calls=[];vm.runInNewContext(src,{$request:{url},$response:{body:typeof body==='string'?body:JSON.stringify(body)},console:{log(){}},$done:r=>calls.push(r)});assert.equal(calls.length,1);return calls[0].body===undefined?body:JSON.parse(calls[0].body);}
const url='https://m5-zb.amap.com/ws/promotion-web/resource/home';
const body={code:1,result:true,data:{banner:{result:true,data:{banner:[{title:'福利中心'},{title:'出行优惠套餐'}],callback:'normal metadata'}},other:{data:{icon:{icon_list:[{title:'unknown entrance'}]}}},order:{id:'keep'}}};const expected=JSON.parse(JSON.stringify(body));expected.data.banner.data.banner=[];
for(const u of [url,url+'?auth=test'])assert.deepStrictEqual(run(u,body),expected);
for(const u of ['https://m5-zb.amap.com/ws/boss/order/car/configInfo','https://m5-zb.amap.com/ws/promotion-web/resource','https://m5.amap.com/ws/promotion-web/resource/home'])assert.deepStrictEqual(run(u,body),body);
for(const b of [null,{data:{}},{data:{banner:{data:{banner:null}}}},'<html>error</html>'])assert.deepStrictEqual(run(url,b),b);
console.log('9 taxi promotion/business/host/boundary cases passed.');
