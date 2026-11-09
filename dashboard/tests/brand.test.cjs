const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const target = path.join(__dirname, '../assets/brand.js');
test('brand config exists and rejects executable colors and remote logos', () => {
  assert.ok(fs.existsSync(target), 'brand implementation is missing');
  const b = require(target);
  const safe = b.normalize({name:'<img onerror=alert(1)>',accent:'red; color:red',accent_soft:'url(https://invalid.test)',logo_url:'https://invalid.test/logo.svg',agents:[{id:'mira',label:'Coordinator'}]});
  assert.equal(safe.name, '<img onerror=alert(1)>');
  assert.equal(safe.accent, '#fa754e');
  assert.equal(safe.accent_soft, '#54b77d');
  assert.equal(safe.logo_url, '');
  assert.equal(safe.agents[0].label,'Coordinator');
  assert.equal(safe.agents.length,5);
  assert.equal(b.normalize({logo_url:'/assets/logo.svg',accent:'#123ABC'}).logo_url,'/assets/logo.svg');
  for(const logo of ['//host/assets/logo.svg','/assets/../private.svg','data:image/svg+xml,abc','/assets/logo.svg?x=1','/assets/%2e%2e/logo.svg']) assert.equal(b.normalize({logo_url:logo}).logo_url,'');
});
