const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app.js','utf8');
const els={ocrtext:{value:'腾讯截图'},ocrcompany:{value:'腾讯'},ocrconfirmation:{classList:{add(){}}}};
let config,calls=0;
const ctx={busy:false,$:id=>els[id],notice:()=>{},fillForm:c=>config=c,investigate:async()=>{calls++;}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('async function confirmOcrCompany'),source.indexOf('function shareImage')),ctx);
(async()=>{
 assert.equal(calls,0);
 await ctx.confirmOcrCompany();assert.equal(calls,1);assert.equal(config.company,'腾讯');assert.equal(config.aliases.length,0);assert.equal(config.question,'');assert.equal(config.official_domain,'');assert.equal(config.mode,'deep');
 ctx.busy=true;await ctx.confirmOcrCompany();assert.equal(calls,1);
 ctx.busy=false;els.ocrtext.value='';await ctx.confirmOcrCompany();assert.equal(calls,1);
 els.ocrtext.value='有文字';els.ocrcompany.value='';await ctx.confirmOcrCompany();assert.equal(calls,1);
 console.log('OCR confirmation, context reset and busy/blank guards passed');
})();
