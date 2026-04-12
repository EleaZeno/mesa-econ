
var GFIELD='gdp';
var GTIMER=null;
var GPLAY=false;

(function(){
  var CFG=[["gdp","GDP","#16a34a"],["unemployment","失业率(%)","#dc2626"],["gini","基尼系数","#9333ea"],["stock_price","股价","#d97706"],["price_index","物价指数","#64748b"],["chart_vol","波动率","#f97316"],["chart_bdr","坏账率(%)","#ec4899"],["loans","信贷总量","#0891b2"],["chart_systemic","系统风险","#dc2626"]];
  var SID=['n_households','n_firms','tax_rate','base_interest_rate','min_wage','productivity','gov_purchase','shock_prob'];
  var SCEN={'经济危机':{'base_interest_rate':15,'tax_rate':25},'宽松政策':{'base_interest_rate':1,'tax_rate':5,'subsidy':15},'高税高补贴':{'tax_rate':40,'subsidy':20,'min_wage':15},'自由市场':{'tax_rate':5,'base_interest_rate':2,'subsidy':0,'min_wage':0},'政府刺激':{'gov_purchase':150,'tax_rate':12,'subsidy':8},'金融危机':{'base_interest_rate':20,'tax_rate':30,'shock_prob':15}};
  refresh();
  setInterval(refresh, 2000);
  loadAgents();
})();

function refresh(){
  var x=new XMLHttpRequest();
  x.open('GET','/api/state',true);
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      try{
        var d=JSON.parse(x.responseText);
        window._HIST=d.history||[];
        document.getElementById('cycle').textContent='第 '+(d.cycle||0)+' 轮';
        updateStats(d.last);
        updateChart(d.history);
      }catch(e){console.error(e);}
    }
  };
  x.send();
}

function updateStats(s){
  if(!s)return;
  var cards=[
    ['gdp','GDP',fk(s.gdp),''],
    ['price_index','物价',(s.price_index||100).toFixed(1),''],
    ['stock_price','股价',(s.stock_price||0).toFixed(1),''],
    ['unemployment','失业率',(s.unemployment||0)+'%',parseFloat(s.unemployment)>15?'danger':''],
    ['gini','基尼',(s.gini||0).toFixed(3),parseFloat(s.gini)>0.4?'warn':''],
    ['emp_rate','就业率',(s.emp_rate||0)+'%',''],
    ['chart_vol','波动率',(s.chart_vol||0).toFixed(3),(s.chart_vol||0)>0.3?'danger':'warn'],
    ['chart_bdr','坏账率',(s.chart_bdr||0).toFixed(1)+'%',(s.chart_bdr||0)>10?'danger':'warn'],
    ['chart_systemic','风险',(s.chart_systemic||0).toFixed(3),(s.chart_systemic||0)>0.2?'danger':'warn'],
    ['bankrupt','破产',s.bankrupt||0,s.bankrupt>0?'warn':''],
    ['loans','贷款',fk(s.loans),''],
    ['govt_rev','政府收入',fk(s.govt_rev),''],
  ];
  document.getElementById('stats').innerHTML=cards.map(function(c){
    return '<div class=scard><div class=slbl2>'+c[0]+'</div><div class="sval '+c[3]+'">'+c[2]+'</div></div>';
  }).join('');
}

function fk(n){
  n=n||0;
  if(n>=1e6)return(n/1e6).toFixed(1)+'M';
  if(n>=1e3)return(n/1e3).toFixed(0)+'K';
  return n.toFixed(0);
}

function updateChart(h){
  var data=(h||[]).map(function(x){return x[GFIELD]||0;});
  if(!data.length)data=[0];
  var color='#3b82f6';
  for(var i=0;i<CFG.length;i++){
    if(CFG[i][0]===GFIELD){color=CFG[i][2];break;}
  }
  var x=new XMLHttpRequest();
  x.open('POST','/api/svg',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      document.getElementById('chart').innerHTML=x.responseText;
    }
  };
  x.send(JSON.stringify({field:GFIELD,values:data,color:color}));
}

function showChart(f){
  GFIELD=f;
  var tabs=document.getElementById('tabs').getElementsByTagName('button');
  for(var i=0;i<tabs.length;i++){
    var fn=tabs[i].getAttribute('onclick')||'';
    tabs[i].className='tabbtn'+(fn.indexOf(f)>0?' active':'');
  }
  updateChart(window._HIST||[]);
}

function step(){
  var x=new XMLHttpRequest();
  x.open('POST','/api/step',true);
  x.onreadystatechange=function(){if(x.readyState===4)refresh();};
  x.send();
}

function togglePlay(){
  var btn=document.getElementById('btn-play');
  if(GPLAY){
    GPLAY=false;clearInterval(GTIMER);
    btn.textContent='播放';btn.className='btn btn-grn';
    var x=new XMLHttpRequest();x.open('POST','/api/pause',true);x.send();
  }else{
    GPLAY=true;
    btn.textContent='暂停';btn.className='btn btn-org';
    var x=new XMLHttpRequest();x.open('POST','/api/play',true);x.send();
    if(GTIMER)clearInterval(GTIMER);
    GTIMER=setInterval(refresh,500);
  }
}

function reset(){
  if(GPLAY){GPLAY=false;clearInterval(GTIMER);}
  document.getElementById('btn-play').textContent='播放';
  document.getElementById('btn-play').className='btn btn-grn';
  var params={};
  for(var i=0;i<SID.length;i++){
    var el=document.getElementById('s-'+SID[i]);
    if(el)params[SID[i]]=parseFloat(el.value);
  }
  var x=new XMLHttpRequest();
  x.open('POST','/api/reset',true);
  x.setRequestHeader('Content-Type','application/json');
  x.onreadystatechange=function(){if(x.readyState===4){refresh();}};
  x.send(JSON.stringify(params));
}

function sl(el,key){
  var disp=document.getElementById('v-'+key);
  if(disp)disp.textContent=el.value;
  var x=new XMLHttpRequest();
  x.open('POST','/api/param',true);
  x.setRequestHeader('Content-Type','application/json');
  x.send(JSON.stringify({key:parseFloat(el.value)}));
}

function applyScen(){
  var nm=document.getElementById('scen').value;
  var p=SCEN[nm];
  if(!p)return;
  for(var k in p){
    var el=document.getElementById('s-'+k);
    if(el){el.value=p[k];var disp=document.getElementById('v-'+k);if(disp)disp.textContent=p[k];}
    var x=new XMLHttpRequest();
    x.open('POST','/api/param',true);
    x.setRequestHeader('Content-Type','application/json');
    x.send(JSON.stringify({key:parseFloat(p[k])}));
  }
}

function loadAgents(){
  var t=encodeURIComponent(document.getElementById('atype').value);
  var x=new XMLHttpRequest();
  x.open('GET','/api/agents/'+t,true);
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      var a=JSON.parse(x.responseText)||[];
      document.getElementById('alist').innerHTML=a.map(function(s){return'<div class=aitem>'+s+'</div>';}).join('')||'<div style="color:#94a3b8">无</div>';
    }
  };
  x.send();
}

function exportCSV(){
  var x=new XMLHttpRequest();
  x.open('GET','/api/state',true);
  x.onreadystatechange=function(){
    if(x.readyState===4&&x.status===200){
      var d=JSON.parse(x.responseText)||{};
      var h=d.history||[];
      if(!h.length)return;
      var keys=Object.keys(h[0]);
      var csv='cycle,'+keys.join(',')+'\n';
      h.forEach(function(r){
        csv+=(r.cycle||'')+','+keys.map(function(k){return r[k]||0;}).join(',')+'\n';
      });
      var a=document.createElement('a');
      a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));
      a.download='economy.csv';a.click();
    }
  };
  x.send();
}
