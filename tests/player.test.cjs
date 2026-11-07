const assert = require('assert'), fs = require('fs'), vm = require('vm');
const elements = {};
const element = id => elements[id] ||= {textContent:'', value:0, disabled:false, paused:true, ended:false, playbackRate:1, duration:360, currentTime:0,
 events:{}, addEventListener(n,f){this.events[n]=f}, setAttribute(){}, load(){this.loadCount=(this.loadCount||0)+1}, pause(){this.paused=true}, play(){this.paused=false; return Promise.resolve()}};
const queue=[], urls=[];
const ctx=vm.createContext({console, Date, Intl, Number, String, Math, Error, AbortController, document:{getElementById:element,addEventListener(){},visibilityState:'visible'},window:{addEventListener(){}},setTimeout(){return 1},clearTimeout(){},setInterval(){},fetch:async url=>{urls.push(url); if(!queue.length) return new Promise(()=>{}); return queue.shift();}});
const source=fs.readFileSync('player.js','utf8').replace(/refresh\(\);\s*$/, '');
vm.runInContext(source,ctx);
function js(code){return vm.runInContext(code,ctx)}
const base=Date.parse('2026-11-07T12:00:00Z');
assert.equal(js(`isStale({target:${base-3600000}, saved:${base-3500000}}, ${base+11*60000})`),false);
assert.equal(js(`isStale({target:${base-3600000}, saved:${base-3500000}}, ${base+12*60000})`),true);
assert.equal(js(`isStale({target:${base}, saved:${base+360000}}, ${base+12*60000})`),false);
assert.equal(js(`isStale({target:null, saved:${base-80*60000}}, ${base})`),true);
const sha1='a'.repeat(40), sha2='b'.repeat(40);
function item(sha){return {sha,commit:{committer:{date:'2026-11-07T12:06:00Z'}}}}
function ok(body){return {ok:true,status:200,json:async()=>body}}
(async()=>{
 queue.push(ok([item(sha1)]),ok({target_epoch:base/1000,intro_detected:true}));
 await js('refresh()');
 assert.equal(elements.audio.src,`https://raw.githubusercontent.com/amitseren-droid/radio-news-recorder/${sha1}/recordings/latest.mp3`);
 assert.equal(elements.play.disabled,false);
 elements.audio.paused=false;
 queue.push(ok([item(sha2)]),{ok:false,status:404});
 await js('refresh()');
 assert(elements.audio.src.includes(sha1));
 assert(elements.status.textContent.includes('לא תיעצר'));
 elements.audio.ended=true; elements.audio.paused=true; elements.audio.events.ended();
 assert(elements.audio.src.includes(sha2));
 queue.push({ok:false,status:403});
 await js('refresh()');
 assert(elements.status.textContent.includes('403'));
 assert(elements.audio.src.includes(sha2));
 assert.equal(elements.refresh.disabled,false);
 assert(urls.every(u=>u.startsWith('https://')));
 console.log('Player tests passed: freshness, grace period, legacy fallback, pinned audio, uninterrupted playback, and API error.');
})().catch(err=>{console.error(err);process.exitCode=1});
