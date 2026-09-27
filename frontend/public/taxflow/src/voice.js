// Shared push-to-talk voice layer (Phase 1). Browser speech recognition first,
// otherwise records with MediaRecorder and hands the blob to opts.transcribe().
(function(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  const MAX_RECORD_MS=60000;
  const LANG_KEY='taxflow_voice_lang';
  const MUTE_KEY='taxflow_voice_muted';

  function store(key,value){
    try{
      if(value===undefined)return localStorage.getItem(key);
      localStorage.setItem(key,value);
    }catch(err){/* storage unavailable */}
    return null;
  }

  let companyLang='';
  function defaultLang(){
    const saved=store(LANG_KEY);
    if(saved==='en-US'||saved==='ar-AE')return saved;
    if(companyLang)return companyLang;
    return String(navigator.language||'').toLowerCase().startsWith('ar')?'ar-AE':'en-US';
  }

  let active=null; // {stop(), cancel()} for the current session

  const VoiceInput={
    supported:()=>!!SR||!!(navigator.mediaDevices&&window.MediaRecorder),
    browserSTT:()=>!!SR,
    isActive:()=>!!active,
    getLang:defaultLang,
    setLang(lang){store(LANG_KEY,lang==='ar-AE'?'ar-AE':'en-US');},
    // Settings > AI & Voice default; used until the user picks a language themselves.
    setCompanyLang(lang){companyLang=(lang==='en-US'||lang==='ar-AE')?lang:'';},
    // opts: {lang, onText(text), onError(msg), onState('listening'|'processing'|'idle'), transcribe(blob,lang)->Promise<text>}
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
    rec.interimResults=false;
    rec.maxAlternatives=1;
    rec.continuous=false;
    let text='',cancelled=false,errored=false;
    rec.onresult=e=>{text=Array.from(e.results).map(r=>r[0].transcript).join(' ').trim();};
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
      if(!cancelled&&text)opts.onText&&opts.onText(text);
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
    rec.ondataavailable=e=>{if(e.data&&e.data.size)chunks.push(e.data);};
    rec.onstop=async()=>{
      clearTimeout(timer);
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
