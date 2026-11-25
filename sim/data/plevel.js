// ===== 永久角色等級（每殺一隻怪＋打贏地圖首領都有經驗；目前只開放到 Lv50）=====
// 所有數字集中在這個檔案。標【原版】＝來自 L1JTW 380 伺服器原始碼；標【自訂】＝《天堂倖存者》自己的規則。
const PL=(function(){
  const MAXLV=50,CAP=999,FREE_FROM=2,PTS_PER_LV=0;   // 【自訂】每升一級（Lv2 起）給 2 點素質點；單項不設上限
  // 【自訂】推薦等級：依地圖編號。沒列到的地圖用 DEFAULT_REC
  const REC={0:9,1:11,2:15,4:17,807:11,808:17,809:16,810:18,811:18,812:20,813:21,4001:22,47:19,48:23,51:32,25:13,26:12,27:31,28:32,4100:19,53:14,54:18,55:25,56:27,4201:32,4202:34,4203:62,30:24,31:28,32:29,33:30,35:36,36:38,37:88,4301:14,19:12,20:15,21:19,59:22,60:25,61:30,63:37,252:25,253:30,254:33,4401:40,65:91};   // 每張圖的推薦等級＝該地圖怪物清單的原版平均等級（四捨五入）；龍之谷地監 7F 只有安塔瑞斯 Lv88
  const DEFAULT_REC=10;
  // 【原版】升級經驗表（累積值），來自 ExpTable.java；EXPT[n-1]＝到達 n 級需要的累積經驗
  // 【自訂】升級經驗表（累積值）：EXPT[n-1]＝到達 n 級需要的累積經驗。依「一局約 2 萬隻怪」設計，各段需要打幾局見對話紀錄
  const EXPT=[0, 40, 110, 210, 335, 490, 675, 890, 1130, 1510, 2220, 3480, 5605, 9045, 14410, 22525, 34525, 51725, 75925, 109425, 155025, 216125, 296925, 401925, 537925, 711925, 931925, 1207925, 1550925, 1974925, 2493925, 3125925, 3890925, 4810925, 5910925, 7150925, 8590925, 10230925, 12090925, 14170925, 16300925, 18490925, 20730925, 23020925, 25370925, 29370925, 34130925, 39710925, 48240925, 59130925];
  // 【自訂】每隻怪的永久經驗（依怪物分類 × 所在地圖推薦等級 ÷ 9）。普通怪基本 2、強化怪 3、小王 20；隨機菁英怪是同一隻怪的 2 倍
  const MEXP={"45005":2,"45008":2,"45009":2,"45010":2,"45011":2,"45012":2,"45016":2,"45021":2,"45023":2,"45065":2,"45060":2,"45024":2,"45068":2,"45107":2,"45121":2,"45122":2,"45126":2,"45136":2,"45157":2,"45161":2,"45173":2,"45184":2,"45215":2,"45278":4,"45392":4,"45019":2,"45131":2,"45130":3,"45241":4,"45083":4,"45509":4,"45115":4,"45945":4,"45190":4,"45259":5,"45264":5,"97294":3,"45381":5,"45948":6,"45946":5,"45947":5,"45949":11,"45950":7,"45951":11,"45082":3,"97290":3,"97302":7,"97304":7,"45027":4,"45041":3,"45092":4,"45142":3,"45156":4,"45329":4,"45276":4,"45321":4,"45324":4,"45420":4,"45032":3,"45125":3,"45168":3,"45170":3,"45169":4,"45187":4,"45243":6,"45283":6,"45282":6,"45281":6,"45345":6,"9102":5,"9103":5,"9101":5,"9104":7,"9105":7,"9106":8,"9107":8,"9113":14,"9115":138,"9112":138,"9110":138,"9111":138,"9114":138,"45280":5,"45289":6,"45373":6,"45393":7,"45451":8,"45043":3,"45551":3,"45098":3,"45127":3,"45143":3,"45149":3,"45171":3,"9203":3,"9201":3,"9204":3,"9202":3,"45213":4,"45307":5,"45144":5,"45920":6,"45921":5,"45922":6,"45923":6,"45926":6,"45924":7,"45928":7,"45164":7,"45929":7,"45930":7,"45349":7,"45927":8,"45925":8,"45933":8,"45934":8,"45936":8,"45937":8,"45938":8,"45939":8,"45940":8,"101010":9,"101011":9,"101003":8,"101007":8,"101002":8,"101005":8,"101006":8,"101001":7,"45803":6,"45804":6,"45805":6,"45807":6,"45808":6,"45809":6,"45817":6,"45826":8,"45806":6,"45810":7,"45811":7,"45812":7,"45813":7,"45816":7,"45818":7,"45819":7,"45822":7,"45823":7,"45814":7,"45815":7,"45820":7,"45821":7,"45824":7,"45874":7,"45825":7,"45827":7};
  // 【自訂】地圖首領固定經驗（100～500，依地圖難度）
  const BXP={"0":100,"1":110,"2":130,"4":140,"19":115,"20":130,"21":150,"25":120,"26":115,"27":210,"28":215,"30":175,"31":195,"32":200,"33":205,"35":235,"36":245,"37":500,"47":150,"48":170,"51":215,"53":125,"54":145,"55":180,"56":190,"807":110,"808":140,"809":135,"810":145,"811":145,"812":155,"813":160,"4001":165,"4100":150,"4201":215,"4202":225,"4203":370,"4301":125,"59":160,"60":170,"61":185,"63":240,"252":200,"253":200,"254":200,"4401":250,"65":520};
  const bossXp=mapId=>BXP[mapId]||100,monXp=(def,elite)=>((def&&MEXP[def.id])||2)*(elite?2:1);
  const need=l=>l>=MAXLV?Infinity:EXPT[l]-EXPT[l-1];   // 【原版】升到下一級所需
  // 【原版】升級時 HP／MP 增加（CalcStat.calcStatHp / calcStatMp）。職業編號：0王族 1騎士 2妖精 3法師 4黑妖 5龍騎 6幻術
  const CT={royal:0,knight:1,elf:2,mage:3,darkelf:4,dk:5,ill:6},ctype=()=>CT[window.__ck]!=null?CT[window.__ck]:2;
  const HPB=[11,17,10,7,10,13,9];                          // 每級基本 HP（再 +0~1 隨機，體質>15 每點再 +1）
  const MAXHP=[2800,3200,2400,2000,2400,2600,2200],MAXMP=[1000,800,1600,2400,1200,1200,2200];   // 【原版】本伺服器 charsettings.properties 的職業上限
  const BASE_HP=100,BASE_MP=40;                            // 【自訂】本遊戲的起始 HP／MP（原版起始只有十幾點）
  function seedYZ(w){let y=0,z=0;
    if(w<9||(w>9&&w<12))y=2;else if(w==9||(w>=12&&w<=17))y=3;
    else if((w>=18&&w<=23)||w==25||w==26||w==29||w==30||w==33||w==34)y=4;else y=5;
    if(w>=7&&w<=9)z=0;else if(w>=10&&w<=14)z=1;else if(w>=15&&w<=20)z=2;else if(w>=21&&w<=24)z=3;else if(w>=25&&w<=28)z=4;else if(w>=29&&w<=32)z=5;else if(w>=33)z=6;
    return [y,z];}
  const MPF=[x=>x,x=>Math.trunc(x*2/3),x=>Math.trunc(x*1.5),x=>x*2,x=>Math.trunc(x*1.5),x=>Math.trunc(x*2/3),x=>Math.trunc(x*5/3)];
  const rint=n=>Math.floor(Math.random()*n);
  const origOf=(c,cs)=>{const C=cs||{};return STAT.orig(c,{str:C.str||8,dex:C.dex||12,con:C.con||12,wis:C.wis||12,int:C.int||12});};
  const orig0=()=>origOf(ctype(),window.__cs);   // 創角素質的原始加成
  function lvHp(con,cOv,orOv){const c=cOv!=null?cOv:ctype(),o=orOv||orig0();return Math.max(0,con-15)+HPB[c]+rint(2)+o.hpUp;}
  function lvMp(wis,cOv,orOv){const c=cOv!=null?cOv:ctype(),o=orOv||orig0(),[y,z]=seedYZ(wis);return MPF[c](rint(y)+1+z)+o.mpUp;}
  // 【自訂】素質點的效果（每一點）
  // 【自訂】素質點效果（每一點）：力量＝近戰傷害 +1%、敏捷＝遠距離傷害 +1%、智力＝技能傷害 +1%、智慧＝每秒回復護盾 +0.025%（最大值）、體質＝每秒回血 +0.025%（最大值）、魅力＝吸取寶物距離 +3%
const FX={str:{melee:0.01},dex:{ranged:0.01},int:{skill:0.01},wis:{mpr:0.00025},con:{hpr:0.00025},cha:{magnet:0.03}};
  const KEYS=['str','dex','con','wis','int','cha'],NAME={str:'力量',dex:'敏捷',con:'體質',wis:'精神',int:'智力',cha:'魅力'};
  const TT=k=>{const o=window.__tmst;return o?(o[k]|0):0;};   // 天賦樹「素質」路線加的素質：效果跟升級點相同
  let S={lv:1,exp:0,pts:0,add:{str:0,dex:0,con:0,wis:0,int:0,cha:0},hpG:0,mpG:0};
  const key=()=>{let a='',n='';try{a=localStorage.getItem('l1s_acct')||'';n=localStorage.getItem('l1s_name')||'';}catch(e){}return 'l1s_pl@'+a+'@'+n;};   // 與 game.js 的 ckey 同一格式（帳號＋角色名各自一份）
  const baseCon=()=>((window.__cs&&window.__cs.con)||12)+S.add.con,baseWis=()=>((window.__cs&&window.__cs.wis)||12)+S.add.wis;
  const free=lv=>Math.max(0,(lv-FREE_FROM+1)*PTS_PER_LV);   // 2級=2點、3級=4點……（點數一律由等級算出）
  function capGain(){const c=ctype();S.hpG=Math.min(S.hpG,MAXHP[c]-BASE_HP);S.mpG=Math.min(S.mpG,MAXMP[c]-BASE_MP);}
  function load(){
    ownKey=key();let o=null;try{o=JSON.parse(localStorage.getItem(ownKey)||'null');}catch(e){}
    S={lv:1,exp:0,pts:0,add:{str:0,dex:0,con:0,wis:0,int:0,cha:0},hpG:0,mpG:0,mig:1};let _mig=false;
    if(o&&typeof o==='object'){
      S.lv=Math.max(1,Math.min(MAXLV,o.lv|0||1));S.exp=(o.lv|0)>MAXLV?0:Math.max(0,+o.exp||0);
      for(const k of KEYS)S.add[k]=Math.max(0,Math.min(CAP,(o.add&&o.add[k])|0));
      if(o.hpG!=null&&o.mpG!=null){S.hpG=Math.max(0,+o.hpG||0);S.mpG=Math.max(0,+o.mpG||0);}
      else{   // 舊存檔沒有成長紀錄：用期望值補上
        const c=ctype(),[y,z]=seedYZ(baseWis());
        S.hpG=Math.round((S.lv-1)*(Math.max(0,baseCon()-15)+HPB[c]+0.5));S.mpG=Math.round((S.lv-1)*MPF[c](((y+1)/2)+z));}
      capGain();
    }
    if(KEYS.some(k=>S.add[k]>0)){for(const k of KEYS)S.add[k]=0;_mig=true;}   // 2026.11.12 素質點取消：已分配的一律歸零（素質改由天賦樹「素質」路線取得）   // 2026.11.12 一次性：已點的素質點全部歸還（點數由等級算出，等於全部可重新分配）
    if(_mig)save();
    if(KEYS.reduce((a,k)=>a+S.add[k],0)>free(S.lv)){for(const k of KEYS)S.add[k]=0;save();}   // 舊版錯誤多給的點數：全部收回，讓玩家重新分配
    S.pts=free(S.lv)-KEYS.reduce((a,k)=>a+S.add[k],0);if(S.pts<0)S.pts=0;   // 點數一律由等級算出，不存「剩餘點數」，避免被改
  }
  let ownKey=null;   // 目前記憶體裡的等級資料是屬於哪個角色的；換角色後絕不能把舊角色的等級寫進新角色
  function save(){try{const k=key();if(ownKey!==null&&ownKey!==k)return;localStorage.setItem(k,JSON.stringify({lv:S.lv,exp:S.exp,add:S.add,hpG:S.hpG,mpG:S.mpG,mig:S.mig|0}));}catch(e){}}
  const rec=mapId=>REC[mapId]!=null?REC[mapId]:DEFAULT_REC;
  // 加經驗並升級；回傳 {gain,lv,up,hp,mp,pts,exp,need}
  function applyExpObj(st,gain0,c,cs){
    const before=st.lv,C=cs||{},or=origOf(c,C);let hp=0,mp=0,pts=0;
    if(st.lv<MAXLV){st.exp+=gain0;
      while(st.lv<MAXLV&&st.exp>=need(st.lv)){st.exp-=need(st.lv);st.lv++;
        const h=lvHp((C.con||12)+(st.add.con||0),c,or),m=lvMp((C.wis||12)+(st.add.wis||0),c,or);
        const h2=Math.max(0,Math.min(h,MAXHP[c]-BASE_HP-st.hpG)),m2=Math.max(0,Math.min(m,MAXMP[c]-BASE_MP-st.mpG));
        st.hpG+=h2;st.mpG+=m2;hp+=h2;mp+=m2;if(st.lv>=FREE_FROM)pts+=PTS_PER_LV;}
      if(st.lv>=MAXLV)st.exp=0;}
    st.pts=free(st.lv)-KEYS.reduce((a,k)=>a+(st.add[k]||0),0);if(st.pts<0)st.pts=0;
    return {gain:gain0,lv:st.lv,before,up:st.lv-before,hp,mp,pts,exp:st.exp,need:need(st.lv)};
  }
  function addExp(gain0){return applyExpObj(S,gain0,ctype(),window.__cs);}
  // 擊敗首領：固定經驗，立刻存檔
  function bossWin(mapId,def){
    const r=addExp(bossXp(mapId));r.rec=rec(mapId);r.mul=1;save();dirty=false;return r;
  }
  // 殺一般怪：存檔延後（每 1.5 秒一次），免得每殺一隻就寫一次
  let dirty=false;
  function kill(def,elite){
    const r=addExp(monXp(def,elite));dirty=true;
    if(r.up>0){save();dirty=false;}
    return r;
  }
  function flushSave(){if(dirty){dirty=false;save();}}
  // 伺服器回傳存檔時：如果伺服器的總經驗和我們回報時一致，代表只是畫面比較新，保留畫面上的資料；不一致才採用伺服器的
  let repTot=null;
  const total=()=>EXPT[Math.max(0,Math.min(MAXLV,S.lv)-1)]+S.exp;
  function markReport(){flushSave();repTot=total();}
  function syncServer(){
    let o=null;try{o=JSON.parse(localStorage.getItem(key())||'null');}catch(e){}
    if(o&&repTot!=null){
      const lv=Math.max(1,Math.min(MAXLV,o.lv|0||1)),sv=EXPT[lv-1]+(+o.exp||0);
      if(Math.abs(sv-repTot)<=1&&total()>=sv){if(o.hpG!=null)S.hpG=Math.max(0,+o.hpG||0);if(o.mpG!=null)S.mpG=Math.max(0,+o.mpG||0);dirty=true;repTot=null;return;}   // 經驗一致時保留畫面上的等級進度，但 HP／MP 成長一律用伺服器算的（2026-11-14 伺服器權威）
    }
    repTot=null;load();
  }
  const hpMp=()=>({hp:S.hpG,mp:S.mpG});
  function addPoint(k){
    if(!KEYS.includes(k)||S.pts<=0||S.add[k]>=CAP)return false;
    S.add[k]++;S.pts--;save();return true;
  }
  function resetPoints(){for(const k of KEYS)S.add[k]=0;S.pts=free(S.lv);save();return true;}   // 洗點：免費，全部收回重分配
  function setLv(target){
    const t=Math.max(1,Math.min(MAXLV,Math.floor(+target)||1));
    S.lv=1;S.exp=0;S.hpG=0;S.mpG=0;
    while(S.lv<t)addExp(need(S.lv));
    save();dirty=false;return S;
  }
  return {S:()=>S,load,save,setLv,resetPoints,bossWin,kill,flushSave,markReport,syncServer,hpMp,addPoint,need,rec,KEYS,NAME,CAP,MAXLV,FX,bossXp,monXp,free,
    melee:()=>1+(S.add.str+TT('str'))*FX.str.melee,ranged:()=>1+(S.add.dex+TT('dex'))*FX.dex.ranged,skill:()=>1+(S.add.int+TT('int'))*FX.int.skill,magic:()=>1+(S.add.int+TT('int'))*FX.int.skill,conHp:()=>0,wisMp:()=>0,
    hpRegenPct:()=>(S.add.con+TT('con'))*FX.con.hpr,mpRegenPct:()=>(S.add.wis+TT('wis'))*FX.wis.mpr,magnetMul:()=>1+(S.add.cha+TT('cha'))*FX.cha.magnet,
    DESC:{str:'近戰傷害 +1%',dex:'遠距離傷害 +1%',int:'技能傷害 +1%',wis:'每秒回復護盾 +0.025%（最大護盾）',con:'每秒回血 +0.025%（最大生命）',cha:'吸取寶物距離 +3%'}};
})();
