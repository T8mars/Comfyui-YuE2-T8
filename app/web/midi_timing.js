/* Pure timing transforms shared by the editor and regression tests. */
(() => {
  'use strict';
  function secondsAt(doc,tick){let time=0,previous=0,tempo=doc.tempos[0].tempo;for(const e of doc.tempos){if(e.tick>tick)break;time+=(e.tick-previous)/doc.ppq*tempo/1e6;previous=e.tick;tempo=e.tempo;}return time+(tick-previous)/doc.ppq*tempo/1e6;}
  function tickAt(doc,seconds){let time=0,previous=0,tempo=doc.tempos[0].tempo;for(const e of doc.tempos){const next=time+(e.tick-previous)/doc.ppq*tempo/1e6;if(next>seconds)return previous+(seconds-time)*1e6/tempo*doc.ppq;time=next;previous=e.tick;tempo=e.tempo;}return previous+(seconds-time)*1e6/tempo*doc.ppq;}
  function regrid(doc,bpm){
    if(!Number.isFinite(bpm)||bpm<20||bpm>400)throw new Error('BPM 应为 20–400');
    const tempo=Math.round(60e6/bpm),convert=tick=>Math.round(secondsAt(doc,tick)*doc.ppq*1e6/tempo);
    const result=structuredClone(doc);
    for(const t of result.tracks){for(const n of t.notes){const end=convert(n.tick+n.duration);n.tick=convert(n.tick);n.duration=Math.max(1,end-n.tick);}for(const e of t.events||[])e.tick=convert(e.tick);}
    // Rounded adjacent meter changes can collide; retain the last declaration.
    result.meters=[...new Map(doc.meters.map(e=>[convert(e.tick),{...e,tick:convert(e.tick)}])).values()];
    for(const k of ['loop_start','loop_end','view_beat'])if(Number.isFinite(result.settings[k]))result.settings[k]=convert(doc.settings[k]*doc.ppq)/doc.ppq;
    result.tempos=[{tick:0,tempo}];
    return result;
  }
  function shift(doc,ids,ms){
    if(!Number.isFinite(ms)||Math.abs(ms)>60000)throw new Error('整体位移应在 −60000 到 60000 毫秒之间');
    const result=structuredClone(doc),chosen=new Set(ids);
    for(const t of result.tracks)for(const n of t.notes)if(chosen.has(n.id)){
      const start=secondsAt(doc,n.tick)+ms/1000,end=secondsAt(doc,n.tick+n.duration)+ms/1000;
      if(start<0)throw new Error('位移会把音符移到音频起点之前；请缩小数值，原音符保留');
      n.tick=Math.round(tickAt(doc,start));n.duration=Math.max(1,Math.round(tickAt(doc,end))-n.tick);
    }
    return result;
  }
  function quantize(doc,ids,division,strength){
    if(![4,8,12,16,24,32].includes(division)||!Number.isFinite(strength)||strength<0||strength>100)throw new Error('请选择有效网格和 0–100% 量化强度');
    const result=structuredClone(doc),chosen=new Set(ids),unit=doc.ppq*4/division;
    for(const t of result.tracks)for(const n of t.notes)if(chosen.has(n.id)){
      const target=Math.floor(n.tick/unit+.5)*unit;
      n.tick=Math.max(0,Math.round(n.tick+(target-n.tick)*strength/100));
      // Move the complete note. Quantization must not turn a short articulation
      // into a full-grid note, or erase a gap/rest by stretching its offset.
    }
    return result;
  }
  globalThis.MidiTiming=Object.freeze({secondsAt,tickAt,regrid,shift,quantize});
})();
