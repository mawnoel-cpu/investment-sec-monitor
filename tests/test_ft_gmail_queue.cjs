"use strict";
const test=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const source=fs.readFileSync("apps-script/FT_Gmail_Queue.gs","utf8");
const FIXED_NOW=Date.parse("2026-10-15T16:00:00Z");

function environment(subjects,opts={}) {
  const counts={flush:0,search:[],gmailMutations:0,triggers:0};
  const props={};
  const pages=subjects.map((subject,i)=>{
    const when=Date.parse("2026-10-14T15:00:00Z") + i*1000;
    return {getMessages(){return [message(subject, opts.domains?.[i]||"ft.com", i,when)];}};
  });
  const sheets={};
  const defaults={
    "Portfolio":[
      ["PSX","Phillips 66",.13,.13],
      ["MSFT","Microsoft",.15,.15],
      ["META","Meta Platforms A",.05,.12],
      ["TLW:LSE","Tullow Oil PLC",.08,.08],
      ["BHP:LSE","BHP Group",.10,.10]
    ],
    "CANDIDATE POOL":null,
    "Sources":null,
  };
  let corrupt=false, failWrite=Boolean(opts.failWrite);
  class FakeSheet {
    constructor(name){this.name=name;this.grid=[];this.maxRows=1000;}
    getMaxRows(){return this.maxRows;}
    insertRowsAfter(start,count){
      assert.equal(start,this.maxRows);
      this.maxRows+=count;
    }
    getLastRow(){
      return this.grid.reduce((max,row,i)=>row?.some(x=>x!==undefined && x!=="") ? i+1:max,0);
    }
    getRange(...args){
      const s=this;
      return {
        setValues(values){
          if (s.name==="FT GMAIL QUEUE" && args[0]>1 && failWrite) throw Error("simulated Sheets failure");
          let start=typeof args[0]==="number"?args[0]:1;
          let column=typeof args[1]==="number"?args[1]:1;
          for(let i=0;i<values.length;i++){
            if(!s.grid[start+i-1]) s.grid[start+i-1]=[];
            for(let j=0;j<values[i].length;j++)
              s.grid[start+i-1][column+j-1]=values[i][j];
          }
          return this;
        },
        getValues(){
          if(typeof args[0]==="string") {
            const ref=args[0];
            if(s.name==="Portfolio" && ref==="A8:D19") return defaults.Portfolio;
            if(s.name==="CANDIDATE POOL" && ref==="A5:A104")
              return [["NVDA"],["ANET"],["ETN"]];
            if(s.name==="CANDIDATE POOL" && ref==="AJ5:AK104")
              return [["Focus 1","1"],["Focus 4","4"],["Screen only",""]];
            if(s.name==="Sources" && ref==="B42")
              return [[new Date("2026-10-14T21:46:06Z")]];
          }
          const start=args[0], col=args[1], count=args[2],width=args[3];
          return Array.from({length:count},(_,i)=>Array.from({length:width},(_,j)=>
            s.grid[start+i-1]?.[col+j-1] ?? ""));
        },
        getValue(){return this.getValues()[0][0];},
        setFontWeight(){return this;},
        setDataValidation(){return this;}
      };
    }
    setFrozenRows(){}
  }
  const workbook={
    getSheetByName(name){return sheets[name]||null;},
    insertSheet(name){return sheets[name]=new FakeSheet(name);}
  };
  Object.keys(defaults).forEach(n=>sheets[n]=new FakeSheet(n));
  class FakeDate extends Date {
    constructor(...args){super(...(args.length?args:[FIXED_NOW]));}
    static now(){return FIXED_NOW;}
  }
  const context={
    Date:FakeDate, JSON, Object, String, RegExp, Number, Math, Error,
    console:{log(){}},
    SpreadsheetApp:{
      openById(){return workbook;},
      flush(){counts.flush++;},
      newDataValidation(){return {requireValueInList(){return this;},build(){return {};}}}
    },
    PropertiesService:{getScriptProperties(){return {
      getProperty(k){return props[k]??null;},
      setProperty(k,v){props[k]=v;},
      deleteProperty(k){delete props[k];}
    };}},
    GmailApp:{
      search(q,offset,count){counts.search.push({q,offset,count});return pages.slice(offset,offset+count);},
      getMessagesForThreads(ts){return ts.map(t=>t.getMessages());},
      getMessageById(id){return pages.flatMap(t=>t.getMessages()).find(x=>x.getId()===id)||null;}
    },
    LockService:{getScriptLock(){return {tryLock(){return true;},releaseLock(){}};}},
    Utilities:{formatDate(){return "2026-10-15";}},
    ScriptApp:{
      getProjectTriggers(){return [];},
      newTrigger(){counts.triggers++;return {timeBased(){return this;},everyMinutes(){return this;},create(){}};},
      deleteTrigger(){}
    }
  };
  vm.createContext(context);
  vm.runInContext(source,context,{filename:"FT_Gmail_Queue.gs"});
  return {context,props,workbook,sheets,counts,setFail:v=>{failWrite=v;}};
}
function message(subject,domain,i,when){
  return {
    getId(){return "msg"+i;},
    getDate(){return new Date(when);},
    getSubject(){return subject;},
    getFrom(){return "Updates <newsletter@"+domain+">";},
    getPlainBody(){return "[AI infrastructure financing faces new questions about capex and issuer obligations while company earnings diverge.](https://seekingalpha.example/article)";}
  };
}
test("collector stages only relevant metadata, not Gmail mutations",()=>{
  const e=environment(["MSFT earnings beat","Reader dinner reservations",
    "BHP:LSE bond credit risk","Semiconductor AI from a publisher"],
    {domains:["ft.com","personal.test","ft.com","personal.test"]});
  const result=e.context.ftGmailQueueCollect();
  assert.equal(result.status,"COMPLETE DISCOVERY");
  const sheet=e.sheets["FT GMAIL QUEUE"];
  assert.equal(sheet.getLastRow(),3);
  assert.equal(sheet.grid[1][2],"P1");
  assert.equal(sheet.grid[2][2],"P1");
  assert.equal(sheet.grid[1][6],"PENDING");
  assert.equal(e.counts.gmailMutations,0);
  assert.equal(e.counts.triggers,0);
  assert.equal(typeof e.props["ft.game.gmail.queue.last_complete.v1"],"string");
});

test("75-thread cap saves cursor and next invocation resumes without duplicates",()=>{
  const subjects=Array.from({length:81},(_,i)=>"MSFT earnings #"+i);
  const e=environment(subjects);
  const a=e.context.ftGmailQueueCollect();
  assert.equal(a.status,"PARTIAL DISCOVERY");
  assert.equal(a.nextOffset,75);
  assert.equal(e.sheets["FT GMAIL QUEUE"].getLastRow(),76);
  const b=e.context.ftGmailQueueCollect();
  assert.equal(b.status,"COMPLETE DISCOVERY");
  assert.equal(e.sheets["FT GMAIL QUEUE"].getLastRow(),82);
  assert.equal(e.props["ft.game.gmail.queue.cursor.v1"],undefined);
  const c=e.context.ftGmailQueueCollect();
  assert.equal(c.queued,0);
  assert.equal(e.sheets["FT GMAIL QUEUE"].getLastRow(),82);
});

test("queue grows its grid before crossing the default 1,000 rows",()=>{
  const subjects=Array.from({length:1025},(_,i)=>"MSFT earnings #"+i);
  const e=environment(subjects);
  let result;
  for(let run=0;run<25;run++){
    result=e.context.ftGmailQueueCollect();
    if(result.status==="COMPLETE DISCOVERY") break;
  }
  assert.equal(result.status,"COMPLETE DISCOVERY");
  assert.equal(e.sheets["FT GMAIL QUEUE"].getLastRow(),1026);
  assert.ok(e.sheets["FT GMAIL QUEUE"].getMaxRows()>=1026);
});

test("failed append does not advance completed-sweep or offset checkpoint",()=>{
  const e=environment(["MSFT earnings"],{failWrite:true});
  assert.throws(()=>e.context.ftGmailQueueCollect(),/simulated Sheets failure/);
  assert.equal(e.props["ft.game.gmail.queue.last_complete.v1"],undefined);
  assert.equal(e.props["ft.game.gmail.queue.cursor.v1"],undefined);
  e.setFail(false);
  assert.equal(e.context.ftGmailQueueCollect().status,"COMPLETE DISCOVERY");
});

test("unrelated mail is excluded; candidate priority is not a held-stock score",()=>{
  const e=environment([]);
  const classify=e.context.ftQueueClassify_;
  const u=e.context.ftQueueUniverse_(e.workbook);
  assert.equal(classify("Trip photos","relative@personal.test",u),null);
  assert.equal(classify("ANET earnings","x@broker.test",u).priority,"P2");
  assert.equal(classify("Phillips 66 guidance","x@broker.test",u).priority,"P1");
  assert.equal(classify("Economic risks","x@ft.com",u).priority,"P3");
});

test("trigger installation is explicit and does not follow collection",()=>{
  const e=environment(["META earnings"]);
  e.context.ftGmailQueueCollect();
  assert.equal(e.counts.triggers,0);
  assert.match(e.context.ftGmailQueueInstallTrigger().status,/INSTALLED/);
  assert.equal(e.counts.triggers,1);
});

test("email subjects cannot become executable spreadsheet formulas",()=>{
  const e=environment(['=IMPORTXML("https://example.invalid","//x")',
    '  =1+1','+SUM(1,2)','@SUM(1,2)','-1+2','Ordinary market update']);
  e.context.ftGmailQueueCollect();
  const rows=e.sheets["FT GMAIL QUEUE"].grid.slice(1);
  assert.equal(rows.length,6);
  rows.slice(0,5).forEach(row=>assert.ok(row[5].startsWith("'")));
  assert.equal(rows[5][5],'Ordinary market update');
});


test("Seeking Alpha snippets become unverified zero-weight Evidence with idempotent queue status",()=>{
  const e=environment(["AI infrastructure funding risk emerging"],{domains:["seekingalpha.com"]});
  e.workbook.insertSheet("Evidence");
  const one=e.context.ftGmailQueueCollect();
  assert.equal(one.research.status,"SCREEN COMPLETE");
  assert.equal(one.research.saved,1);
  const q=e.sheets["FT GMAIL QUEUE"].grid[1];
  assert.equal(q[6],"SCREENED");
  assert.match(q[9],/zero weight/i);
  const record=e.sheets["Evidence"].grid[7];
  assert.equal(record[0],"GMAIL.SA.PREVIEW.msg0");
  assert.equal(record[9],"Unverified");
  assert.equal(record[6],0);
  assert.match(record[10],/J8="Valid"/);
  assert.equal(record[14],"msg0");
  assert.equal(record[16],"Excerpt");
  assert.equal(e.counts.gmailMutations,0);
  const again=e.context.ftGmailQueueScreen();
  assert.equal(again.saved,0);
  assert.equal(e.sheets["Evidence"].getLastRow(),8);
});

test("a topic headline without article text is only a research lead",()=>{
  const e=environment([]);
  const c=e.context;
  assert.equal(c.ftResearchTheme_("AI capex financing strain"),"AI_FINANCING_RISK");
  assert.equal(c.ftResearchTheme_("Healthcare rotation"),"HEALTHCARE_ROTATION");
  assert.equal(c.ftResearchTheme_("Oil price and energy"),"ENERGY_ROTATION");
  const item=c.ftResearchEvidenceRow_(["msg1","","","","seekingalpha.com","Rocket Lab delay"],
    "Rocket Lab delay","",new Date("2026-10-14"),new Date("2026-10-15"));
  const row=c.ftQueueScreenEvidenceCells_(item,308);
  assert.equal(row[16],"Headline only");
  assert.equal(row[9],"Unverified");
  assert.equal(row[6],0);
  assert.equal(row[14],"msg1");
});

test("Seeking Alpha welcome promotions are excluded without Evidence, but a mixed retirement digest is not",()=>{
  const e=environment(["You're in. Here's where to start.","How To Retire: Hope For 95, Prepare For 105"],
    {domains:["seekingalpha.com","seekingalpha.com"]});
  e.workbook.insertSheet("Evidence");
  const outcome=e.context.ftGmailQueueCollect();
  assert.equal(outcome.research.excluded,1);
  assert.equal(outcome.research.saved,1);
  assert.equal(e.sheets["Evidence"].getLastRow(),8);
  const queue=e.sheets["FT GMAIL QUEUE"].grid.slice(1);
  assert.equal(queue[0][6],"EXCLUDED");
  assert.equal(queue[1][6],"SCREENED");
});
