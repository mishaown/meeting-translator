// DOM interaction checks with an in-memory API fixture; this does not record audio.
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const html=fs.readFileSync('web/index.html','utf8');
const dom=new JSDOM(html,{runScripts:'outside-only',pretendToBeVisual:true,url:'http://127.0.0.1:8765'});
const w=dom.window, d=w.document;
const $=id=>d.getElementById(id);
const settings={source:'mic',model:'base',translation_model:'qwen25-1.5b',device:'auto',transcription:true,translation:true,diarization:false};
let job=null, setupRequest=null;
let setupState={state:'idle',message:'Choose models',data_directory:'test-data',error:null};
const recording={id:'test_meeting',title:'Test meeting',source:'mic',created_at:'2026-10-28T16:00:00+08:00',duration:5,tracks:['mixed'],speakers:{},rows:[{id:'r1',start:2,end:4,speaker:'speaker_1',chinese:'大家好 <script>unsafe</script>',english:'Hello everyone.',status:'ready'}]};
recording.active_run='aaaaaaaaaaaa';
recording.runs=[{id:'aaaaaaaaaaaa',active:true,options:settings,caption_count:1,created_at:recording.created_at,elapsed_seconds:2},{id:'bbbbbbbbbbbb',active:false,options:{model:'tiny',translation_model:'whisper'},caption_count:1,created_at:recording.created_at,elapsed_seconds:1}];
w.HTMLMediaElement.prototype.pause=function(){};w.HTMLMediaElement.prototype.load=function(){};w.HTMLMediaElement.prototype.play=function(){this.playCalled=true;return Promise.resolve();};
w.HTMLCanvasElement.prototype.getContext=function(){return {setTransform(){},clearRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}};};
w.HTMLDialogElement.prototype.showModal=function(){this.setAttribute('open','');};w.HTMLDialogElement.prototype.close=function(){this.removeAttribute('open');};
w.fetch=async (url,options={})=>{const body=JSON.parse(options.body||'{}');let data;
  if(url==='/api/status')data={job,defaults:settings};
  else if(url==='/api/models')data={transcription:[{id:'base',name:'Whisper base',installed:true},{id:'tiny',name:'Whisper tiny',installed:true},{id:'missing',name:'Missing',installed:false}],translation:[{id:'qwen25-1.5b',name:'Qwen2.5 1.5B',installed:true},{id:'whisper',name:'Direct Whisper',installed:true}],devices:[{id:'auto',name:'Automatic'},{id:'cpu',name:'CPU'},{id:'gpu',name:'GPU'}]};
  else if(url==='/api/setup'){if(options.method==='POST'){setupRequest=body;setupState={...setupState,state:'complete',message:'Models ready'};}data=setupState;}
  else if(url==='/api/sessions')data={sessions:[{...recording,rows:undefined}]};
  else if(url==='/api/start'){job={id:recording.id,state:'recording',mode:'record',message:'Listening',duration:1,level:.1,pending:0,options:{...settings,...body}};data=job;}
  else if(url==='/api/pause'){job.state='paused';data={job};}
  else if(url==='/api/resume'){job.state='recording';data={job};}
  else if(url==='/api/stop'){job.state='complete';data={job};}
  else if(url==='/api/options'){Object.assign(settings,body);if(job)Object.assign(job.options,body);data={job,defaults:settings};}
  else if(url.endsWith('/rename')){recording.title=body.title;data=recording;}
  else if(url.endsWith('/speakers')){Object.assign(recording.speakers,body.speakers);data=recording;}
  else if(url.endsWith('/speaker')){recording.rows.find(r=>r.id===body.row_id).speaker=body.speaker;data=recording;}
  else if(url.endsWith('/runs/bbbbbbbbbbbb'))data={...recording.runs[1],rows:[{start:2,chinese:'另一个结果',english:'Alternative model result.'}]};
  else if(url.endsWith('/select-run')){recording.active_run=body.run_id;recording.runs.forEach(run=>run.active=run.id===body.run_id);data=recording;}
  else if(url==='/api/sessions/test_meeting')data=recording;
  else throw Error('Unexpected test URL: '+url);
  return {ok:true,json:async()=>JSON.parse(JSON.stringify(data))};
};
const errors=[];w.addEventListener('error',e=>errors.push(e.error));
const tick=()=>new Promise(resolve=>setTimeout(resolve,40));
async function click(id){$(id).click();await tick();}
(async()=>{
  w.eval(fs.readFileSync('web/app.js','utf8'));await tick();
  assert.equal(d.querySelector('[data-source=mic]').getAttribute('aria-pressed'),'true');assert.equal($('toggle-diarization').checked,false);
  d.querySelector('[data-source=both]').click();assert.equal(d.querySelector('[data-source=both]').getAttribute('aria-pressed'),'true');
  d.querySelector('[data-source=mic]').click();$('meeting-title').value='A real meeting';await click('start');
  assert.equal(job.options.source,'mic');assert.equal($('pause').disabled,false);assert.equal($('start').disabled,true);
  assert.equal($('model').disabled,false);assert.equal($('model').querySelector('[value=missing]').disabled,true);
  $('model').value='tiny';$('model').dispatchEvent(new w.Event('change'));await tick();assert.equal(job.options.model,'tiny');
  $('translation-model').value='whisper';$('translation-model').dispatchEvent(new w.Event('change'));await tick();assert.equal(job.options.translation_model,'whisper');
  await click('pause');assert.match($('pause').textContent,/Resume/);await click('pause');assert.match($('pause').textContent,/Pause/);
  $('toggle-translation').click();await tick();assert.equal(job.options.translation,false);assert.equal(job.options.transcription,true);assert.equal($('conversation').classList.contains('hide-en'),true);
  $('toggle-transcription').click();await tick();assert.equal(job.options.transcription,false);assert.match($('processing-note').textContent,/Audio will still be recorded/);
  await click('stop');assert.equal($('saved-view').classList.contains('hidden'),false);assert.match($('player').src,/test_meeting\/audio\/mixed$/);
  assert.equal(d.querySelectorAll('script').length,2);assert.match(d.querySelector('.caption-text.zh').textContent,/<script>unsafe<\/script>/);
  d.querySelector('.timestamp').click();assert.equal($('player').currentTime,2);assert.equal($('player').playCalled,true);
  $('show-english').click();assert.equal($('conversation').classList.contains('hide-en'),true);$('show-english').click();
  await click('rename');$('rename-input').value='Research discussion';$('rename-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal(recording.title,'Research discussion');assert.equal($('saved-title').textContent,'Research discussion');
  await click('manage-speakers');d.querySelector('.speaker-name').value='王老师';d.querySelector('.speaker-label').value='Female';$('speakers-form').dispatchEvent(new w.Event('submit',{cancelable:true}));await tick();assert.equal(recording.speakers.speaker_1.name,'王老师');assert.equal(recording.speakers.speaker_1.label,'Female');
  $('compare-run').value='bbbbbbbbbbbb';$('compare-run').dispatchEvent(new w.Event('change'));await tick();assert.equal($('comparison').classList.contains('hidden'),false);assert.match($('comparison').textContent,/Alternative model result/);assert.match($('comparison').textContent,/Hello everyone/);
  $('result-run').value='bbbbbbbbbbbb';$('result-run').dispatchEvent(new w.Event('change'));await tick();assert.equal(recording.active_run,'bbbbbbbbbbbb');assert.equal($('comparison').classList.contains('hidden'),true);
  w.eval(fs.readFileSync('web/setup.js','utf8'));await tick();await click('setup-open');assert.equal($('setup-dialog').open,true);$('setup-profile').value='lightweight';$('setup-profile').dispatchEvent(new w.Event('change'));assert.equal($('setup-speakers').checked,false);await click('setup-download');assert.equal(setupRequest.profile,'lightweight');assert.equal(setupRequest.speakers,false);assert.equal($('setup-use').classList.contains('hidden'),false);await click('setup-close');assert.equal($('setup-dialog').open,false);
  assert.deepEqual(errors,[]);console.log('PASS: source selection, recording controls, model selection/download setup, saved playback, seeking, speaker labels, comparisons and text escaping.');
  dom.window.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
