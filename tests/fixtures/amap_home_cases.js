const fs=require('fs'),vm=require('vm'),assert=require('assert');const src=fs.readFileSync(__dirname+'/../../quantumultx/scripts/amap-page-cleanup.js','utf8');
function run(body,url='https://m5.amap.com/ws/info_bff/ContentCenter'){let calls=[];vm.runInNewContext(src,{$request:{url},$response:{body:JSON.stringify(body)},console:{log(){}},$done:r=>calls.push(r)});assert.equal(calls.length,1);return calls[0].body===undefined?body:JSON.parse(calls[0].body);}
const body={page:'ContentCenter:29544',data:{modules:{header:{data:{list:['关注','推荐','附近','美食'].map(title=>({title}))}},waterfall:{data:{list:[{title:'normal post'},{title:'promotion'}],hasMore:'1',total:20,page_index:'0'}},other:{data:{search:'keep'}}},regions:{header:['header'],waterfall:['waterfall'],content:['header','other','waterfall']},meta:{guide:'keep'}}};
const expected=JSON.parse(JSON.stringify(body));expected.data.modules.header.data.list=[];expected.data.modules.waterfall.data.list=[];expected.data.modules.waterfall.data.hasMore='0';expected.data.modules.waterfall.data.total=0;expected.data.regions={header:[],waterfall:[],content:['other']};assert.deepStrictEqual(run(body),expected);
for(const hasMore of [true,1]){const input=JSON.parse(JSON.stringify(body));input.data.modules.waterfall.data.hasMore=hasMore;const output=run(input);assert.strictEqual(output.data.modules.waterfall.data.hasMore,typeof hasMore==='boolean'?false:0);}
const reviews=JSON.parse(JSON.stringify(body));reviews.page='ContentCenter:99999';assert.deepStrictEqual(run(reviews),reviews);
const unrelated=JSON.parse(JSON.stringify(body));unrelated.data.modules.header.data.list=[{title:'点评'},{title:'照片'}];assert.deepStrictEqual(run(unrelated),unrelated);
const missing=JSON.parse(JSON.stringify(body));delete missing.data.modules.header;assert.deepStrictEqual(run(missing),missing);
assert.deepStrictEqual(run(body,'https://m5.amap.com/ws/shield/search_poi/search/sp'),body);
console.log('7 home feed/scope/pagination/business cases passed.');
