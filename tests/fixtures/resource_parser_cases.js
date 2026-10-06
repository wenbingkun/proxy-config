// Execute the real reviewed parser with public, synthetic inputs and no network.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const parser = fs.readFileSync(process.argv[2], 'utf8');
const root = process.argv[3];
function parse(type, link, content) {
  const calls = [];
  const context = {
    $resource: {type, link, content, tag: 'public-fixture'},
    $environment: {version: '1.5.6 build999'},
    $prefs: {valueForKey: () => undefined, setValueForKey: () => true},
    $notify: () => {}, $done: value => calls.push(value),
    $task: {fetch: () => {throw Error('fixture attempted network access');}},
    console: {log: () => {}}, TextEncoder, TextDecoder, URL, Buffer,
    setTimeout: () => {throw Error('unexpected asynchronous parser action');},
  };
  vm.runInNewContext(parser, context, {timeout: 3000});
  assert(calls.length > 0, 'parser did not complete');
  // The existing upstream may invoke $done more than once; all outputs must agree.
  assert(calls.every(value => value.content === calls[0].content), 'conflicting parser completions');
  assert.strictEqual(typeof calls[0].content, 'string');
  return calls[0].content;
}
const cf = ['HOST-SUFFIX,cloudflare.com,PROXY', 'HOST,fixture.cloudflare.com,PROXY',
  'IP-CIDR,1.1.1.0/24,PROXY', 'IP6-CIDR,2606:4700::/32,PROXY', 'IP-ASN,13335,PROXY'].join('\n');
const filtered = parse('filter', 'https://fixture.invalid/Cloudflare.list#out=IP-CIDR+IP6-CIDR+IP-ASN&ntf=0', cf);
assert(!/^(IP-CIDR|IP6-CIDR|IP-ASN),/im.test(filtered), filtered);
assert(/cloudflare\.com/i.test(filtered) && /fixture\.cloudflare\.com/i.test(filtered), filtered);
const node = Buffer.from(parse('server', 'https://fixture.invalid/public-nodes', 'trojan://fixture-password@node.invalid:443?peer=node.invalid#Fixture'), 'base64').toString('utf8');
assert(/trojan=node\.invalid:443/.test(node), node);
assert(/password=fixture-password/.test(node) && /tag=Fixture/.test(node), node);
const mixed = fs.readFileSync(root + '/quantumultx/rewrite/fmz200-Zhihu.snippet', 'utf8');
const link = 'https://fixture.invalid/Zhihu.snippet';
const broken = parse('rewrite', link, mixed);
assert(/^(USER-AGENT|IP6-CIDR),[^\n]*url reject/im.test(broken), 'negative control no longer reproduces the mixed-resource bug');
const fixed = parse('rewrite', link + '#regout=^(USER-AGENT|IP6-CIDR)%2C&ntf=0', mixed);
assert(!/^(USER-AGENT|IP6-CIDR),/im.test(fixed), fixed);
assert(!/^(USER-AGENT|IP6-CIDR),[^\n]*url reject/im.test(fixed), fixed);
function meaningful(output) {
  return output.split(/\r?\n/).map(line => line.trim().replace(/^hostname\s*=\s*/, 'hostname=')).filter(line => line && !/^(#|;)/.test(line));
}
const expected = meaningful(broken).filter(line => !/^(USER-AGENT|IP6-CIDR),/i.test(line));
assert.deepStrictEqual(meaningful(fixed), expected, 'normal rewrites or MitM changed');
const rules = parse('filter', link, mixed);
assert(/^(user-agent|USER-AGENT),\s*AVOS\*,\s*reject/im.test(rules), rules);
assert(/^(ip6-cidr|IP6-CIDR),\s*2402:4e00:1200:ed00:0:9089:6dac:96b6\/128,\s*reject/im.test(rules), rules);
console.log('Real parser: Cloudflare exclusions, Trojan conversion, Zhihu dual loading and negative control passed.');
