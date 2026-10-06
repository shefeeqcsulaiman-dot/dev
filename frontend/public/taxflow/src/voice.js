// Shared push-to-talk voice layer (Phase 1). Browser speech recognition first,
// otherwise records with MediaRecorder and hands the blob to opts.transcribe().
(function(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  const MAX_RECORD_MS=60000;
  const SILENCE_STOP_MS=1500;   // recorder fallback: stop this long after speech ends
  const NO_SPEECH_STOP_MS=8000; // ...or if nothing is said at all
  const MUTE_KEY='taxflow_voice_muted';

  function store(key,value){
    try{
      if(value===undefined)return localStorage.getItem(key);
      localStorage.setItem(key,value);
    }catch(err){/* storage unavailable */}
    return null;
  }

  // Voice is English-only.
  function defaultLang(){return 'en-US';}

  let active=null; // {stop(), cancel()} for the current session

  const VoiceInput={
    supported:()=>!!SR||!!(navigator.mediaDevices&&window.MediaRecorder),
    browserSTT:()=>!!SR,
    isActive:()=>!!active,
    getLang:defaultLang,
    setLang(){},
    setCompanyLang(){},
    // opts: {lang, onText(text), onInterim(text), onError(msg), onState('listening'|'processing'|'idle'), transcribe(blob,lang)->Promise<text>}
    start(opts={}){
      if(active){active.stop();return;}
      const lang=opts.lang||defaultLang();
      const state=s=>{try{opts.onState&&opts.onState(s);}catch(err){console.warn(err);}};
      const fail=msg=>{active=null;state('idle');opts.onError&&opts.onError(msg);};
      if(SR)return startBrowser(opts,lang,state,fail);
      return startRecorder(opts,lang,state,fail);
    },
    stop(){if(active)active.stop();},
    cancel(){if(active)active.cancel();}
  };

  function startBrowser(opts,lang,state,fail){
    const rec=new SR();
    rec.lang=lang;
    rec.interimResults=true;   // live words while speaking (opts.onInterim)
    rec.maxAlternatives=1;
    rec.continuous=false;
    let text='',live='',cancelled=false,errored=false;
    rec.onresult=e=>{
      const all=Array.from(e.results);
      text=all.filter(r=>r.isFinal).map(r=>r[0].transcript).join(' ').trim();
      live=all.map(r=>r[0].transcript).join(' ').trim();
      if(live&&opts.onInterim){try{opts.onInterim(live);}catch(err){console.warn(err);}}
    };
    rec.onerror=e=>{
      errored=true;
      if(e.error==='no-speech'||e.error==='aborted'){active=null;state('idle');return;}
      // Network/service errors: fall back to server transcription when available.
      if((e.error==='network'||e.error==='service-not-allowed')&&opts.transcribe&&navigator.mediaDevices&&window.MediaRecorder){
        active=null;
        startRecorder(opts,lang,state,fail);
        return;
      }
      fail(e.error==='not-allowed'?'Microphone permission was denied.':`Speech recognition error: ${e.error}`);
    };
    rec.onend=()=>{
      if(errored)return;
      active=null;
      state('idle');
      const said=text||live;   // stopped mid-phrase: keep what was shown live
      if(!cancelled&&said)opts.onText&&opts.onText(said);
    };
    active={stop:()=>rec.stop(),cancel:()=>{cancelled=true;rec.abort();}};
    try{rec.start();state('listening');}catch(err){fail(String(err.message||err));}
  }

  async function startRecorder(opts,lang,state,fail){
    if(!opts.transcribe)return fail('Voice input is not supported in this browser.');
    if(!(navigator.mediaDevices&&window.MediaRecorder))return fail('Voice input is not supported in this browser.');
    let stream;
    active={stop(){},cancel(){}}; // placeholder while the permission prompt is open
    try{stream=await navigator.mediaDevices.getUserMedia({audio:true});}
    catch(err){return fail('Microphone permission was denied.');}
    const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4','audio/ogg'].find(t=>MediaRecorder.isTypeSupported&&MediaRecorder.isTypeSupported(t))||'';
    const rec=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream);
    const chunks=[];
    let cancelled=false;
    const timer=setTimeout(()=>rec.state!=='inactive'&&rec.stop(),MAX_RECORD_MS);
    // Quiet after speech -> transcribe; nothing said at all -> drop it (don't spend a transcription).
    const stopSilence=watchSilence(stream,heard=>{if(!heard)cancelled=true;if(rec.state!=='inactive')rec.stop();});
    rec.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data);};
    rec.onstop=async()=>{
      clearTimeout(timer);
      stopSilence();
      stream.getTracks().forEach(t=>t.stop());
      if(cancelled||!chunks.length){active=null;state('idle');return;}
      state('processing');
      try{
        const text=String(await opts.transcribe(new Blob(chunks,{type:rec.mimeType||'audio/webm'}),lang)||'').trim();
        active=null;
        state('idle');
        if(text)opts.onText&&opts.onText(text);
      }catch(err){fail(String(err.message||err));}
    };
    active={stop:()=>rec.state!=='inactive'&&rec.stop(),cancel:()=>{cancelled=true;if(rec.state!=='inactive')rec.stop();}};
    rec.start();
    state('listening');
  }

  // Stops the recorder once the speaker goes quiet (the browser recognizer does this itself).
  function watchSilence(stream,onSilence){
    const Ctx=window.AudioContext||window.webkitAudioContext;
    if(!Ctx)return ()=>{};
    let ctx,timer=0;
    try{
      ctx=new Ctx();
      if(ctx.state==='suspended')ctx.resume().catch(()=>{});
      const analyser=ctx.createAnalyser();
      analyser.fftSize=1024;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const buf=new Uint8Array(analyser.fftSize);
      const started=performance.now();
      const levels=[]; // [time, rms] since the start
      const tick=()=>{
        analyser.getByteTimeDomainData(buf);
        let sum=0;
        for(let i=0;i<buf.length;i++){const v=(buf[i]-128)/128;sum+=v*v;}
        const now=performance.now();
        levels.push([now,Math.sqrt(sum/buf.length)]);
        // Speech = clearly louder than the quietest moment so far; judged over the whole
        // recording, so talking straight away isn't mistaken for background noise.
        const quietest=Math.min(...levels.map(l=>l[1]));
        const thr=Math.max(0.015,quietest*3);
        let lastLoud=0;
        for(let i=levels.length-1;i>=0;i--){if(levels[i][1]>thr){lastLoud=levels[i][0];break;}}
        if(lastLoud&&now-lastLoud>SILENCE_STOP_MS){clearInterval(timer);onSilence(true);}
        else if(!lastLoud&&now-started>NO_SPEECH_STOP_MS){clearInterval(timer);onSilence(false);}
      };
      timer=setInterval(tick,100);
    }catch(err){return ()=>{};}
    return ()=>{clearInterval(timer);try{ctx&&ctx.close();}catch(err){/* ignore */}};
  }

  const VoiceOutput={
    supported:()=>'speechSynthesis' in window,
    isMuted:()=>store(MUTE_KEY)==='1',
    setMuted(m){store(MUTE_KEY,m?'1':'0');if(m)VoiceOutput.stop();},
    stop(){try{window.speechSynthesis&&window.speechSynthesis.cancel();}catch(err){/* ignore */}},
    speak(text,lang){
      if(!VoiceOutput.supported()||VoiceOutput.isMuted()||!text)return;
      VoiceOutput.stop();
      const u=new SpeechSynthesisUtterance(String(text).slice(0,1500));
      // Arabic text is spoken with an Arabic voice even if the UI language is English.
      u.lang=lang||(/[؀-ۿ]/.test(text)?'ar-AE':'en-US');
      const voice=window.speechSynthesis.getVoices().find(v=>v.lang&&v.lang.toLowerCase().startsWith(u.lang.slice(0,2).toLowerCase()));
      if(voice)u.voice=voice;
      window.speechSynthesis.speak(u);
    }
  };

  window.VoiceInput=VoiceInput;
  window.VoiceOutput=VoiceOutput;
})();
