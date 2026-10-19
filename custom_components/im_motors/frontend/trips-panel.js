const escapeHtml = (value) => String(value ?? "—").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const dayString = (date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,"0")}-${String(date.getDate()).padStart(2,"0")}`;
const stateNames = {idle:"等待同步",running:"正在同步",complete:"同步完成",error:"同步停止"};
const dayStates = {normal:"正常",no_trips:"暂无行程",no_access:"当日未开启权限",unknown:"未知"};
const observations={waiting:"等待首个有效快照",recording:"行程记录中",parked:"驻车，等待下一段行程",cached_or_out_of_order:"云端快照未更新，重复数据已跳过",missing_source_time:"快照缺少云端时间，暂不记录",future_source_time:"云端时间异常，暂不记录",stale_cloud_snapshot:"云端快照过旧，暂不记录",unknown_state:"车辆状态不完整，等待有效数据",recording_failed:"本地写入失败，请检查存储空间"};

class ImMotorsTripsPanel extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode:"open"}); this.entries=[]; this.result=null;
    const today=new Date(); this.startDate=dayString(new Date(today.getFullYear(),today.getMonth(),1)); this.endDate=dayString(today);
  }
  set hass(value) {
    this._hass=value;
    if(!this.initialized && this.isConnected) { this.initialized=true; this.loadEntries(); }
  }
  connectedCallback() {
    if(this._hass && !this.initialized) { this.initialized=true; this.loadEntries(); }
    this.timer=setInterval(()=> {
      if(this.vehicle && !document.hidden && !this.shadowRoot.activeElement?.matches("input,select"))this.query(false);
    },15000);
  }
  disconnectedCallback() { clearInterval(this.timer); this.initialized=false; }
  async loadEntries() {
    try {
      this.entries=await this._hass.callWS({type:"im_motors/trips/entries"});
      this.entry=this.entries[0]; this.vehicle=this.entry?.vehicles?.[0]?.id;
      this.render(); if(this.vehicle) await this.query();
    } catch(error) { this.error=error.message||"加载失败"; this.render(); }
  }
  async query(showLoading=true) {
    if(!this.vehicle || this.querying) return;
    this.querying=true;
    try {
      this.result=await this._hass.callWS({type:"im_motors/trips/query",entry_id:this.entry.entry_id,vehicle_id:this.vehicle,start_date:this.startDate,end_date:this.endDate});
      this.error=null;
    } catch(error) { this.error=error.message||"查询失败"; }
    finally { this.querying=false; this.render(); }
  }
  async sync(geocode=false) {
    try {
      const result=await this._hass.callWS({type:"im_motors/trips/sync",entry_id:this.entry.entry_id,geocode_only:geocode});
      this.notice=result.started?"同步已开始，页面会自动更新。":"同步正在进行。";
      await this.query();
    } catch(error) {this.error=error.message||"同步失败";this.render();}
  }
  async importFile(file) {
    if(!file || !this.vehicle) return;
    try {
      if(file.size>2*1024*1024)throw new Error("单个文件不能超过2MB。");
      const result=await this._hass.callWS({type:"im_motors/trips/import",entry_id:this.entry.entry_id,vehicle_id:this.vehicle,content:await file.text(),format:file.name.toLowerCase().endsWith(".csv")?"csv":"json"});
      this.notice=`已导入 ${result.days} 天、${result.trips} 段行程，重复导入会合并。`;this.error=null;
      await this.query();
    }catch(error){this.error=error.message||"导入失败";this.render();}
  }
  export(format) {
    if(!this.result) return;
    let data, type;
    if(format==="json") { data=JSON.stringify(this.result,null,2);type="application/json"; }
    else {
      const quote=value=>{let text=String(value??"");if(/^[=+@\t\r-]/.test(text))text="'"+text;return '"'+text.replace(/"/g,'""')+'"';};
      const columns=["date","daily_distance_km","daily_duration_minutes","start_time","end_time","start_address","end_address","distance_km","duration_minutes"];
      const rows=this.result.days.flatMap(day=>{const trips=this.result.trips.filter(trip=>trip.date===day.date);return (trips.length?trips:[{}]).map(trip=>({...trip,date:day.date,daily_distance_km:day.distance_km,daily_duration_minutes:day.duration_minutes}));});
      data="\uFEFF"+[columns.map(quote).join(","),...rows.map(row=>columns.map(key=>quote(row[key])).join(","))].join("\r\n");type="text/csv;charset=utf-8";
    }
    const link=document.createElement("a");link.href=URL.createObjectURL(new Blob([data],{type}));link.download=`智己行程-${this.startDate}-${this.endDate}.${format}`;link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);
  }
  render() {
    const e=escapeHtml,r=this.result,s=r?.status||{},stats=r?.stats||{};
    const missingMileage=(r?.days||[]).filter(d=>d.distance_km===null).length;
    const distance=(r?.days||[]).filter(d=>d.distance_km!==null).reduce((sum,d)=>sum+d.distance_km,0);
    const trips=(r?.trips||[]).filter(t=>!this.selectedDay || t.date===this.selectedDay).sort((a,b)=>(b.start_time||"").localeCompare(a.start_time||""));
    this.shadowRoot.innerHTML=`
      <style>
        :host{display:block;color:var(--primary-text-color);background:var(--primary-background-color);min-height:100vh;font:14px var(--paper-font-body1_-_font-family,Roboto,Arial,sans-serif)}
        header{display:flex;align-items:center;gap:16px;padding:12px 20px;background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff);font-size:20px}
        main{max-width:1100px;margin:auto;padding:22px}h1{font-size:24px;margin:0 0 8px}h2{font-size:18px}p{line-height:1.6;color:var(--secondary-text-color)}
        .card{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;padding:18px;margin:16px 0}.toolbar{display:flex;gap:12px;align-items:end;flex-wrap:wrap}
        label{display:flex;flex-direction:column;gap:6px}input,select,button{font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:7px;padding:9px 12px}
        button{cursor:pointer}button.primary{background:var(--primary-color);color:var(--text-primary-color,#fff);border:0}button:disabled{opacity:.45;cursor:default}a{color:var(--primary-color)}
        .stats{display:flex;gap:28px;flex-wrap:wrap}.stats strong{display:block;font-size:25px;margin-bottom:5px}.stats span{color:var(--secondary-text-color)}
        .error{color:var(--error-color,#c62828)}.notice{color:var(--primary-color)}.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;white-space:nowrap}th,td{text-align:left;padding:12px 9px;border-bottom:1px solid var(--divider-color)}th{color:var(--secondary-text-color)}
        tr.selected{background:var(--secondary-background-color)}td button{padding:5px 8px}.trip{border-top:1px solid var(--divider-color);padding:16px 0}.trip:first-of-type{border:0}.times{font-weight:600;margin-bottom:10px}.route{display:grid;grid-template-columns:30px 1fr;gap:10px}.route small{color:var(--secondary-text-color)}.metrics{margin:12px 0 0 40px;color:var(--secondary-text-color)}.empty{padding:28px;text-align:center;color:var(--secondary-text-color)}
        @media(max-width:600px){main{padding:12px}.card{padding:14px}.stats{gap:20px}h1{font-size:21px}input{max-width:145px}.times{font-size:13px}.toolbar{gap:8px}}
      </style>
      <header><hass-toggle-menu></hass-toggle-menu><span>智己历史行程</span></header>
      <main><h1>每日里程与行程档案</h1><p>使用 HA 已有的车辆状态、总里程与位置自行记录未来行程。历史长期保留，不随 HA 状态历史清理；起止时间为采样观测值。</p>
      ${this.error?`<div class="card error" role="alert">${e(this.error)}</div>`:""}
      ${!this.entry?'<div class="card">没有已加载的智己集成，请先配置账号。</div>':`
      <div class="card"><div class="toolbar"><label>账号与车辆<select id="account">${this.entries.flatMap(entry=>entry.vehicles.map(v=>`<option value="${e(entry.entry_id)}:${e(v.id)}" ${entry.entry_id===this.entry.entry_id&&v.id===this.vehicle?"selected":""}>${e(entry.title)} · ${e(v.name)}</option>`)).join("")}</select></label><a href="/config/integrations/integration/im_motors">配置行程同步和高德 Key</a></div>
      <p>${this.entry.enabled?`已启用 HA 自行记录 · 车况查询间隔 ${e(this.entry.interval_minutes)} 分钟`:"尚未启用：请在集成的“配置”页面开启本地行程记录。"} · ${this.entry.amap_key_configured?"已配置高德 Web 服务 Key":"未配置高德 Key：先保存坐标，配置后可补全地址。"}</p>
      <p aria-live="polite"><strong>${e(observations[s.recording?.observation]||"等待采样")}</strong>${s.recording?.active_since?` · 本段首次观测 ${e(s.recording.active_since)}`:""}${s.recording?.last_sample?`<br>最近采用的云端快照：${e(s.recording.last_sample)}`:""}</p>
      <div class="toolbar"><button class="primary" id="sync" ${!this.entry.enabled||s.state==="running"?"disabled":""}>立即采样车况</button><button id="geocode" ${!this.entry.enabled||s.state==="running"||!this.entry.amap_key_configured?"disabled":""}>补全历史地址</button><label>导入历史文件（可选）<input id="import" type="file" accept=".json,.csv,application/json,text/csv" ${!this.entry.enabled||s.state==="running"?"disabled":""}></label></div>
      <details><summary>导入格式说明</summary><p>可导入本页面导出的 JSON/CSV，或按下列格式整理手机截图数据。开始、结束时间需保留完整日期；跨天行程填写次日日期。单个文件最多2MB。CSV 必须包含 date、daily_distance_km；每段行程另填 start_time、end_time、distance_km、duration_minutes、start_address、end_address。日里程请抄录 App 汇总，不要由单次里程相加代替。</p><pre style="overflow:auto">${e(JSON.stringify({days:[{date:"2026-10-18",distance_km:12.3}],trips:[{date:"2026-10-18",start_time:"2026-10-18 23:40:00",end_time:"2026-10-19 00:20:00",distance_km:12.3,duration_minutes:40,start_address:"示例起点",end_address:"示例终点"}]},null,2))}</pre></details>
      <p aria-live="polite">${e(stateNames[s.state]||"等待同步")}${s.current_date?` · ${e(s.current_date)}`:""}${s.processed!==undefined?` · 本次已保存 ${e(s.processed)} 天`:""}${s.finished_at?` · 上次完成 ${e(s.finished_at)}`:""}</p>
      ${s.message?`<p class="error" role="alert">${e(s.message)}</p>`:""}${s.address_message?`<p class="error">${e(s.address_message)}</p>`:""}${this.notice?`<p class="notice">${e(this.notice)}</p>`:""}
      </div>
      <div class="card"><div class="toolbar"><label>开始日期<input type="date" id="start" value="${e(this.startDate)}"></label><label>结束日期<input type="date" id="end" value="${e(this.endDate)}"></label><button class="primary" id="query">查询本地历史</button><button id="csv">导出 CSV</button><button id="json">导出 JSON</button></div><p>本地档案：${e(stats.days??0)} 天 / ${e(stats.trips??0)} 段行程${stats.first_day?` · ${e(stats.first_day)} 至 ${e(stats.last_day)}`:""}。查询历史不请求车辆或高德。</p></div>
      <div class="card stats"><div><strong>${missingMileage===(r?.days?.length||0)&&missingMileage?"—":distance.toFixed(1)+" km"}</strong><span>已记录的已知里程${missingMileage?`（${missingMileage}天缺失）`:""}</span></div><div><strong>${e(r?.days?.length??0)} 天</strong><span>有历史档案的日期</span></div><div><strong>${e(r?.trips?.length??0)} 段</strong><span>已保存行程或推定区间</span></div></div>
      <div class="card"><h2>每日汇总</h2>${r?.days?.length?`<div class="table-scroll"><table><thead><tr><th>日期</th><th>里程 km</th><th>时长 分钟</th><th>行程数</th><th>状态</th></tr></thead><tbody>${r.days.map(d=>`<tr class="${this.selectedDay===d.date?"selected":""}"><td><button data-day="${e(d.date)}">${e(d.date)}</button></td><td>${e(d.distance_km)}</td><td>${e(d.duration_minutes)}</td><td>${e(d.total_count)}</td><td>${e(dayStates[d.status]||d.status)}</td></tr>`).join("")}</tbody></table></div>`:'<div class="empty">当前日期范围没有已入库数据。请同步，或调整查询日期。</div>'}</div>
      <div class="card"><h2>${this.selectedDay?e(this.selectedDay):"所选范围"}的行程 ${this.selectedDay?'<button id="all">显示全部日期</button>':""}</h2>
      ${trips.map(t=>`<article class="trip"><div class="times">${e(t.start_time)} → ${e(t.end_time)}${t.crosses_date?" · 跨天":""}</div><p>${t.detection==="odometer_interval"?"推定区间：两次驻车采样之间新增了里程，可能包含多次短途。":t.source==="ha_telemetry"?"HA 观测行程，起止时间受查询间隔影响。":"导入的历史记录。"}${t.sampling_gap?" 存在采样缺口。":""}${t.partial_start&&t.detection!=="odometer_interval"?" 记录开始时车辆已在行驶。":""}${t.odometer_reset?" 总里程发生回退，未计算本段距离。":""}</p><div class="route"><span>起</span><div>${e(t.start_poi||t.start_address||"地址待补全")}${t.start_poi?`<br><small>${e(t.start_address)}</small>`:""}${!t.start_address&&t.start_coordinates?`<br><small>坐标 ${e(t.start_coordinates.latitude)}, ${e(t.start_coordinates.longitude)}</small>`:""}</div><span>终</span><div>${e(t.end_poi||t.end_address||"地址待补全")}${t.end_poi?`<br><small>${e(t.end_address)}</small>`:""}${!t.end_address&&t.end_coordinates?`<br><small>坐标 ${e(t.end_coordinates.latitude)}, ${e(t.end_coordinates.longitude)}</small>`:""}</div></div><div class="metrics">${e(t.distance_km)} km · ${e(t.duration_minutes)} 分钟${t.source==="ha_telemetry"?" · 总里程分辨率 1 km":""}</div></article>`).join("")||'<div class="empty">没有已结束的行程。开始记录后，观测到行驶并随后驻车时会保存。</div>'}
      <p>里程采用两次总里程读数之差，无法恢复过去两个月。跨天行程按出发日汇总；未观测到的短途可能合并为推定区间。云端根时间未证实为 GPS 独立采样时间，高德 POI 也可能与官方 App 不同。</p></div>`}</main>`;
    const on=(id,event,fn)=>{const el=this.shadowRoot.getElementById(id);if(el)el.addEventListener(event,fn);};
    on("start","change",event=>this.startDate=event.target.value);on("end","change",event=>this.endDate=event.target.value);
    on("query","click",()=>{this.selectedDay=null;this.query();});on("sync","click",()=>this.sync());on("geocode","click",()=>this.sync(true));
    on("csv","click",()=>this.export("csv"));on("json","click",()=>this.export("json"));on("all","click",()=>{this.selectedDay=null;this.render();});
    on("import","change",event=>this.importFile(event.target.files[0]));
    on("account","change",event=>{const [id,vehicle]=event.target.value.split(":");this.entry=this.entries.find(entry=>entry.entry_id===id);this.vehicle=vehicle;this.selectedDay=null;this.query();});
    this.shadowRoot.querySelectorAll("[data-day]").forEach(button=>button.addEventListener("click",()=>{this.selectedDay=button.dataset.day;this.render();}));
    const menu=this.shadowRoot.querySelector("hass-toggle-menu");if(menu)menu.hass=this._hass;
  }
}
if(!customElements.get("im-motors-trips-panel"))customElements.define("im-motors-trips-panel",ImMotorsTripsPanel);
