/**
 * FT GAME — resumable Gmail discovery plus unverified digest screening.
 *
 * STAGED MODULE. Do NOT enable ftGmailQueueInstallTrigger until the existing
 * container-bound script is inspected, synced and authorized by its owner.
 * No automatic install, no Gmail mutations, no scored research or trade edits.
 */
var FT_GMAIL_QUEUE = Object.freeze({
  spreadsheetId: "1ItUi3uYPK4AvuHQ6PHIZTRvQnVhMiLONefTpVLEzGGE",
  sheet: "FT GMAIL QUEUE",
  cursorKey: "ft.game.gmail.queue.cursor.v1",
  lastKey: "ft.game.gmail.queue.last_complete.v1",
  pageSize: 25,
  pagesPerRun: 3,
  overlapSeconds: 2 * 86400,
  initialDays: 14,
  maxRows: 50000, // Safety ceiling; actual queue grid grows as needed.
  deadline: "2026-11-27",
  headers: ["MESSAGE ID", "RECEIVED UTC", "PRIORITY", "TICKERS", "SENDER DOMAIN",
    "SUBJECT", "STATUS", "ATTEMPTS", "UPDATED UTC", "DISPOSITION / ISSUE"]
});

function ftGmailQueueCollect() {
  var result = ftQueueCollect_();
  // Use the EXISTING 30-minute trigger: no second schedule or research system.
  // The research receipt is separate, and a screening error never rewinds discovery.
  try { result.research = ftGmailQueueScreen_(); }
  catch (err) { result.research = {status:"FAILED",error:String(err).slice(0,250)}; }
  console.log(JSON.stringify(result));
  return result;
}

function ftQueueCollect_() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return {status:"BUSY", reason:"Collector lock held"};
  try {
    var now = new Date();
    if (Utilities.formatDate(now, "America/Toronto", "yyyy-MM-dd") >
        FT_GMAIL_QUEUE.deadline) {
      return {status:"SKIPPED", reason:"FT game ended"};
    }
    var props = PropertiesService.getScriptProperties();
    var book = SpreadsheetApp.openById(FT_GMAIL_QUEUE.spreadsheetId);
    var sheet = ftQueueSheet_(book);
    var existing = ftQueueExisting_(sheet);
    var universe = ftQueueUniverse_(book);
    var cursor = ftQueueCursor_(props, book, Math.floor(now.getTime()/1000));
    var count = 0, examined = 0, pages = 0, duplicates = 0;
    var started = Date.now();
    while (pages < FT_GMAIL_QUEUE.pagesPerRun && Date.now()-started < 210000) {
      // Both date bounds are fixed for an entire sweep. New incoming mail cannot
      // shift earlier results to different offsets while the sweep runs.
      var query = "after:" + cursor.since + " before:" + cursor.until +
        " -in:spam -in:trash";
      var threads = GmailApp.search(query, cursor.offset, FT_GMAIL_QUEUE.pageSize);
      var msgs = threads.length ? GmailApp.getMessagesForThreads(threads) : [];
      var added = [];
      for (var t=0; t<msgs.length; t++) {
        for (var m=0; m<msgs[t].length; m++) {
          var msg = msgs[t][m], id = String(msg.getId());
          var when = Math.floor(msg.getDate().getTime()/1000);
          if (when < cursor.since || when >= cursor.until) continue;
          examined++;
          if (existing[id]) {duplicates++; continue;}
          var classified = ftQueueClassify_(msg.getSubject(), msg.getFrom(), universe);
          if (!classified) continue;
          var stamp = new Date().toISOString();
          added.push([id,new Date(when*1000).toISOString(),classified.priority,
            classified.tickers.join(","),classified.domain,
            ftQueueLiteral_(String(msg.getSubject() || "").slice(0,240)),"PENDING",0,stamp,
            "Subject/sender triage only; no original source verified"]);
          existing[id] = true;
        }
      }
      if (added.length) {
        var startRow=sheet.getLastRow()+1;
        var lastNeeded = startRow + added.length - 1;
        if (lastNeeded > FT_GMAIL_QUEUE.maxRows)
          throw new Error("FT GMAIL QUEUE capacity exceeded; checkpoint retained");
        // Native new tabs typically start at 1,000 rows. Grow deliberately
        // before writing; never assume getRange creates missing grid rows.
        if (lastNeeded > sheet.getMaxRows()) {
          var oldMax=sheet.getMaxRows();
          sheet.insertRowsAfter(oldMax, Math.max(200,lastNeeded-oldMax));
          // Preserve native status choices across newly created queue rows.
          var previousStatus=sheet.getRange(Math.max(2,oldMax),7);
          var rule=typeof previousStatus.getDataValidation === "function"
            ? previousStatus.getDataValidation() : null;
          if (rule) sheet.getRange(oldMax+1,7,sheet.getMaxRows()-oldMax,1)
            .setDataValidation(rule);
        }
        sheet.getRange(startRow,1,added.length,FT_GMAIL_QUEUE.headers.length)
          .setValues(added);
        SpreadsheetApp.flush();
        var readback=sheet.getRange(startRow,1,added.length,1).getValues();
        for (var k=0; k<added.length; k++)
          if (String(readback[k][0]) !== added[k][0])
            throw new Error("FT GMAIL QUEUE!A"+(startRow+k)+
              ": append readback mismatch; cursor NOT advanced");
        count += added.length;
      }
      // Only advance after every append from this page is read back.
      cursor.offset += threads.length;
      props.setProperty(FT_GMAIL_QUEUE.cursorKey,JSON.stringify(cursor));
      pages++;
      if (threads.length < FT_GMAIL_QUEUE.pageSize) {
        // Commit the successful fixed-bounds sweep. Two-day overlap and
        // persistent message IDs make retries idempotent.
        props.setProperty(FT_GMAIL_QUEUE.lastKey,String(cursor.until));
        props.deleteProperty(FT_GMAIL_QUEUE.cursorKey);
        return {status:"COMPLETE DISCOVERY",queued:count,examined:examined,
          duplicates:duplicates,lastSearchedBeforeUtc:new Date(cursor.until*1000).toISOString(),
          note:"Metadata queue ONLY; investment research not verified"};
      }
    }
    return {status:"PARTIAL DISCOVERY",queued:count,examined:examined,
      duplicates:duplicates,nextOffset:cursor.offset,
      lastSearchedBeforeUtc:new Date(cursor.until*1000).toISOString()};
  } finally {lock.releaseLock();}
}

// Subjects are untrusted email metadata, never executable spreadsheet formulas.
function ftQueueLiteral_(value) {
  var text = String(value == null ? "" : value);
  return /^\s*[=+@-]/.test(text) ? "'" + text : text;
}

function ftQueueSheet_(book) {
  var sheet=book.getSheetByName(FT_GMAIL_QUEUE.sheet);
  if (!sheet) {
    sheet=book.insertSheet(FT_GMAIL_QUEUE.sheet);
    sheet.getRange(1,1,1,FT_GMAIL_QUEUE.headers.length)
      .setValues([FT_GMAIL_QUEUE.headers]);
    sheet.setFrozenRows(1);
    sheet.getRange(1,1,1,FT_GMAIL_QUEUE.headers.length).setFontWeight("bold");
    sheet.getRange(2,7,sheet.getMaxRows()-1,1).setDataValidation(
      SpreadsheetApp.newDataValidation()
        .requireValueInList(["PENDING","SCREENED","VERIFIED","UNVERIFIED",
          "DEFERRED","EXCLUDED","FAILED"],true).build());
    SpreadsheetApp.flush();
  } else {
    var header=sheet.getRange(1,1,1,FT_GMAIL_QUEUE.headers.length).getValues()[0];
    if (JSON.stringify(header)!==JSON.stringify(FT_GMAIL_QUEUE.headers))
      throw new Error("FT GMAIL QUEUE!A1:J1 schema mismatch; no writes");
  }
  return sheet;
}

function ftQueueExisting_(sheet) {
  var count=sheet.getLastRow()-1, found=Object.create(null);
  if (count > FT_GMAIL_QUEUE.maxRows-1) throw new Error("FT GMAIL QUEUE full");
  if (!count) return found;
  var values=sheet.getRange(2,1,count,1).getValues();
  for (var i=0; i<values.length; i++) if(values[i][0])
    found[String(values[i][0])]=true;
  return found;
}

function ftQueueUniverse_(book) {
  var held=Object.create(null), bench=Object.create(null), aliases=[];
  var portfolio=book.getSheetByName("Portfolio").getRange("A8:D100").getValues();
  for (var i=0; i<portfolio.length; i++) {
    var ticker=String(portfolio[i][0]||"").trim();
    if (ticker==="TOTAL") break;
    if (!ticker) continue;
    var current=Number(portfolio[i][2])||0;
    var queued=Number(portfolio[i][3])||0;
    if (current>0 || queued>0) {
      held[ticker]=true;
      aliases.push({ticker:ticker, name:String(portfolio[i][1]||"").toLowerCase()});
    }
  }
  var pool=book.getSheetByName("CANDIDATE POOL");
  var names=pool.getRange("A5:A104").getValues();
  var priorities=pool.getRange("AJ5:AK104").getValues();
  for (var j=0; j<names.length; j++) {
    var stock=String(names[j][0]||"").trim();
    var rank=String(priorities[j][0]||"");
    if (stock && /^Focus\s*[1-5]$/i.test(rank) && !held[stock]) bench[stock]=true;
  }
  return {held:held,bench:bench,aliases:aliases};
}

function ftQueueClassify_(subject, from, universe) {
  var title=String(subject||""), lower=title.toLowerCase();
  var match=String(from||"").match(/@([a-z0-9.-]+\.[a-z]{2,})/i);
  var domain=match ? match[1].toLowerCase() : "unknown";
  var tickers=[];
  var seen=Object.create(null);
  var all=Object.keys(universe.held).concat(Object.keys(universe.bench));
  for (var i=0; i<all.length; i++) {
    var symbol=all[i];
    var root=symbol.replace(/:LSE$/,"");
    // NOW is an ordinary English word; matching it would label unrelated
    // emails P1/P2 (shopping, streaming, and ticket notices).
    if (root==="NOW" && !(/\bServiceNow\b/i.test(title) || /(?:\$NOW\b|\bNYSE:NOW\b)/i.test(title))) continue;
    if (root.length<3) continue;
    var rex=new RegExp("(^|[^A-Za-z0-9])"+root+"([^A-Za-z0-9]|$)","i");
    if (rex.test(title) && !seen[symbol]) {tickers.push(symbol);seen[symbol]=true;}
  }
  for (var a=0; a<universe.aliases.length; a++) {
    var item=universe.aliases[a];
    var company=item.name.replace(/\s+(plc|inc|corp|corporation|group|a)$/,"").trim();
    if (company.length>=5 && lower.indexOf(company)!==-1 && !seen[item.ticker]) {
      tickers.push(item.ticker); seen[item.ticker]=true;
    }
  }
  var firstPriority=tickers.some(function(t){return universe.held[t];});
  var secondPriority=tickers.some(function(t){return universe.bench[t];});
  var knownPublisher=/(^|\.)(ft\.com|wsj\.com|marketwatch\.com|barrons\.com|bloomberg\.com|substack\.com|seekingalpha\.com|financialtimes\.com|bankofcanada\.ca|bis\.org|federalreserve\.gov|sec\.gov|investor\.|investors\.)/.test(domain);
  var financialTerms=/(earnings|guidance|filing|investor|capital markets|profit warning|credit|interest rates|bond yield|shares|stock market|oil price|energy market|semiconductors|industrial|trade sanctions)/i.test(title);
  if (!firstPriority && !secondPriority && !knownPublisher && !financialTerms)
    return null;
  return {priority:firstPriority ? "P1" : secondPriority ? "P2" : "P3",
    tickers:tickers, domain:domain};
}

function ftQueueCursor_(props,book,nowSecs) {
  var raw=props.getProperty(FT_GMAIL_QUEUE.cursorKey);
  if (raw) {
    var parsed=JSON.parse(raw);
    if (!Number.isInteger(parsed.since) || !Number.isInteger(parsed.until) ||
        !Number.isInteger(parsed.offset) || parsed.since>parsed.until)
      throw new Error("Invalid ft.gmail.cursor; stop instead of skipping mail");
    return parsed;
  }
  var previous=Number(props.getProperty(FT_GMAIL_QUEUE.lastKey));
  if (!(previous>0)) {
    var fromSheet=book.getSheetByName("Sources").getRange("B42").getValue();
    var epoch=fromSheet instanceof Date ? fromSheet.getTime() :
      (typeof fromSheet==="number" ? Date.UTC(1899,11,30)+fromSheet*86400000 :
        Date.parse(String(fromSheet||"")));
    previous=Number.isFinite(epoch) ? Math.floor(epoch/1000) : 0;
  }
  var earliest=nowSecs - FT_GMAIL_QUEUE.initialDays*86400;
  var since=previous>0 ? Math.max(earliest, previous-FT_GMAIL_QUEUE.overlapSeconds)
    : earliest;
  return {version:1,since:Math.floor(since),until:nowSecs,offset:0};
}

/** Read-only queue health, independent of research and trade scoring. */
function ftGmailQueueStatus() {
  var result = ftQueueStatus_();
  console.log(JSON.stringify(result));
  return result;
}

function ftQueueStatus_() {
  var sheet=SpreadsheetApp.openById(FT_GMAIL_QUEUE.spreadsheetId)
    .getSheetByName(FT_GMAIL_QUEUE.sheet);
  if (!sheet) return {status:"NOT DEPLOYED"};
  var rows=sheet.getLastRow()-1, states={};
  if (rows>0) sheet.getRange(2,7,rows,1).getValues().forEach(function(r){
    var state=String(r[0]||"");
    states[state]=(states[state]||0)+1;
  });
  var props=PropertiesService.getScriptProperties();
  return {status:"READ ONLY",count:rows,states:states,
    cursor:props.getProperty(FT_GMAIL_QUEUE.cursorKey)||"",
    lastCompleteEpoch:props.getProperty(FT_GMAIL_QUEUE.lastKey)||""};
}

/** Explicit opt-in only; never installed by ftGmailQueueCollect. */
function ftGmailQueueInstallTrigger() {
  var triggers=ScriptApp.getProjectTriggers().filter(function(t){
    return t.getHandlerFunction()==="ftGmailQueueCollect";
  });
  if (triggers.length) return {status:"ALREADY INSTALLED",count:triggers.length};
  ScriptApp.newTrigger("ftGmailQueueCollect").timeBased().everyMinutes(30).create();
  return {status:"INSTALLED; requires user OAuth authorization"};
}

function ftGmailQueueRemoveTrigger() {
  var triggers=ScriptApp.getProjectTriggers().filter(function(t){
    return t.getHandlerFunction()==="ftGmailQueueCollect";
  });
  triggers.forEach(function(t){ScriptApp.deleteTrigger(t);});
  return {removed:triggers.length};
}


/**
 * Evidence bridge for Seeking Alpha Must Reads and other Seeking Alpha digests.
 * Does NOT read premium articles. Does NOT verify publisher claims, score a
 * direction, touch orders, modify Gmail or create another recurring trigger.
 *
 * Reuse the existing queue, Evidence ledger and time trigger. A headline/preview
 * is a zero-eligible-weight research lead, never a trading signal.
 */
var FT_SA_SCREEN = Object.freeze({maxPerRun:8, maxMessageChars:32000,
  maxExcerptChars:1000, gameEnd:"2026-11-27"});

function ftGmailQueueScreen() {
  var response=ftGmailQueueScreen_();
  console.log(JSON.stringify(response));
  return response;
}

function ftResearchIsPromotion_(subject) {
  return /(?:\bwelcome\b|here's where to start|you're in\.|one step away|\bsubscribe\b|\bfree access\b|\bjoin now\b|\bquiz\b|your favorite|members got|\bdiscount\b|\bspecial offer\b)/i.test(String(subject||""));
}

function ftResearchTheme_(text) {
  text=String(text||"").toLowerCase();
  if (/\bhealthcare\b|\bbiotech\b|\bpharma\b|\binsurer\b/.test(text))
    return "HEALTHCARE_ROTATION";
  if (/\bcredit\b|\bfinanc\w*|capex|\bcash crunch\b|\bai bubble\b/.test(text))
    return "AI_FINANCING_RISK";
  if (/\bsemiconductor\b|\bmicron\b|\bhynix\b|memory price|\bchip\b/.test(text))
    return "SEMICONDUCTOR_CYCLE";
  if (/\bbreadth\b|under the hood|\bconcentration\b|\bmarket leadership\b|\bbear market\b/.test(text))
    return "MARKET_BREADTH";
  if (/\boil\b|\benergy\b|\bnatural gas\b|\br[ea]fin/.test(text))
    return "ENERGY_ROTATION";
  if (/\brocket lab\b|\bneutron\b|\brklb\b/.test(text))
    return "SPACE_VALUATION";
  if (/\bearnings\b|\bupgrade\b|\bvaluation\b/.test(text))
    return "COMPANY_RESEARCH";
  return "MACRO_RESEARCH";
}

function ftResearchQuestion_(theme) {
  var questions={
    HEALTHCARE_ROTATION:"Compare healthcare/insurer relative strength and issuer catalysts against held positions.",
    AI_FINANCING_RISK:"Cross-check AI capex/funding claims against issuer cash flow, debt and funding terms.",
    SEMICONDUCTOR_CYCLE:"Check memory prices, primary results and sector-relative leadership; do not infer future earnings.",
    MARKET_BREADTH:"Verify advance/decline, sector dispersion and relative strength on dated prices.",
    ENERGY_ROTATION:"Check crude/refining trends and cash-flow updates versus the existing energy holdings.",
    SPACE_VALUATION:"Validate RKLB launch timetable, funding and sales multiples against issuer filings.",
    COMPANY_RESEARCH:"Locate named issuer, dated original disclosures and compare entry/valuation.",
    MACRO_RESEARCH:"Identify dated, falsifiable market claims and independent supporting evidence."
  };
  return questions[theme]||questions.MACRO_RESEARCH;
}

function ftResearchDigestPreview_(plain, subject) {
  var lines=String(plain||"").slice(0,FT_SA_SCREEN.maxMessageChars).split(/\r?\n/);
  var chosen=[], seen={};
  for(var i=0;i<lines.length&&chosen.length<4;i++){
    var line=String(lines[i]||"").trim();
    // On Must Reads, full article text is NOT embedded. Extract only
    // visible teaser sentences; never claim to have read linked articles.
    var md=line.match(/^\[([^\]]{35,})\]\(/);
    if(md) line=md[1].trim();
    if(!line||line.length<50||line.length>650||
      /https?:|unsubscribe|premium|special offer|sent by seeking alpha|sponsored|scan to download|your email|\bjoin now\b|\btrading with\b/i.test(line))
      continue;
    if(line.toLowerCase()===String(subject||"").toLowerCase()||seen[line])continue;
    seen[line]=1;chosen.push(line);
  }
  return chosen.join(" | ").slice(0,FT_SA_SCREEN.maxExcerptChars);
}

function ftResearchEvidenceRow_(row, subject, preview, observed, captured) {
  var msgId=String(row[0]), title=String(subject||row[5]||"").slice(0,240);
  var theme=ftResearchTheme_(title+" "+preview);
  var ticker=String(row[3]||"").trim()||"Macro";
  var relevant=ftResearchQuestion_(theme);
  var info="Seeking Alpha email digest ONLY. Subject: "+title+
    ". Preview: "+(preview||"not provided; title only")+
    ". Full linked articles, claims and price targets NOT independently read or verified.";
  var n=0; // Caller substitutes actual row number in formula cells.
  return {ticker:ticker,theme:theme,
    research:"Theme "+theme+"; "+relevant+" DISCOVERY ONLY, no order, 0 eligible research weight.",
    info:ftQueueLiteral_(info.slice(0,1650)),
    title:ftQueueLiteral_(title),
    observed:observed,captured:captured,
    source:"https://mail.google.com/mail/u/0/#all/"+encodeURIComponent(msgId),
    msgId:msgId,extraction:preview?"Excerpt":"Headline only"};
}

function ftQueueScreenEvidenceCells_(item, n) {
  var v=function(x){return x;};
  return [
    "GMAIL.SA.PREVIEW."+item.msgId,item.ticker,item.observed,item.info,item.source,
    "=IFNA(VLOOKUP(M"+n+",Sources!$J$8:$M$200,4,FALSE),0)",
    0,14,"=IF(C"+n+"=\"\",\"\",TODAY()-INT(C"+n+"))",
    "Unverified",
    "=IF(AND(OR(J"+n+"=\"Valid\",J"+n+"=\"Qualified\"),ISNUMBER(C"+n+"),I"+n+">=0,I"+n+"<=H"+n+
      ",OR(Q"+n+"=\"Full\",Q"+n+"=\"Excerpt\",Q"+n+"=\"Data\")),IFERROR(INDEX(\u0027SOURCE CALIBRATION\u0027!$K$5:$K$150,MATCH(M"+n+",\u0027SOURCE CALIBRATION\u0027!$A$5:$A$150,0)),F"+n+")*IF(J"+n+"=\"Qualified\",0.3,1),0)",
    "=K"+n+"*G"+n,
    "Seeking Alpha","Contributor digest / preview",item.msgId,item.captured,
    item.extraction,"GMAIL:SA:"+item.msgId,item.title,item.research
  ];
}

function ftGmailQueueScreen_() {
  var lock=LockService.getScriptLock();
  if(!lock.tryLock(1000))return {status:"BUSY",reason:"Screening lock held"};
  try {
    var today=Utilities.formatDate(new Date(),"America/Toronto","yyyy-MM-dd");
    if(today>FT_SA_SCREEN.gameEnd)return {status:"SKIPPED",reason:"Game ended"};
    var book=SpreadsheetApp.openById(FT_GMAIL_QUEUE.spreadsheetId);
    var queue=book.getSheetByName(FT_GMAIL_QUEUE.sheet);
    var evidence=book.getSheetByName("Evidence");
    if(!queue||!evidence)return {status:"NOT DEPLOYED",reason:"Queue or Evidence missing"};
    var rows=Math.max(0,queue.getLastRow()-1), saved=0, excluded=0,
      duplicates=0, scanned=0, failed=0;
    if(!rows)return {status:"EMPTY",saved:0};
    var q=queue.getRange(2,1,rows,10).getValues();
    // Source identity is the Gmail message ID, NOT the subject. The same
    // subject can recur and different digests may contain identical teasers.
    var erows=evidence.getMaxRows();
    var evidenceIds=evidence.getRange(8,15,erows-7,1).getValues();
    var used=evidence.getRange(8,1,erows-7,1).getValues();
    var seen={};
    var last=7;
    evidenceIds.forEach(function(x){if(x[0])seen[String(x[0])]=true;});
    used.forEach(function(x,i){if(x[0])last=i+8;});
    var target=[];
    for(var i=0;i<q.length;i++){
      var r=q[i];
      if(String(r[4]||"").toLowerCase()!=="seekingalpha.com" ||
        String(r[6])!=="PENDING")continue;
      target.push({index:i,row:r});
    }
    // Check most recent subject-level risk leads first, but retain all other
    // pending records for future runs; do not silently discard older emails.
    target.sort(function(a,b){return String(b.row[1]).localeCompare(String(a.row[1]));});
    for(var j=0;j<target.length&&scanned<FT_SA_SCREEN.maxPerRun;j++){
      var item=target[j], r=item.row, msgId=String(r[0]), qr=item.index+2;
      scanned++;
      try {
        if(seen[msgId]){
          queue.getRange(qr,7,1,4).setValues([["SCREENED",Number(r[7]||0)+1,
            new Date().toISOString(),"Already in Evidence; existing record preserved"]]);
          duplicates++;continue;
        }
        if(ftResearchIsPromotion_(r[5])){
          queue.getRange(qr,7,1,4).setValues([["EXCLUDED",Number(r[7]||0)+1,
            new Date().toISOString(),"Marketing/admin/retirement topic; not an FT Game stock signal"]]);
          excluded++;continue;
        }
        var msg=GmailApp.getMessageById(msgId);
        if(!msg)throw new Error("Gmail message not accessible");
        var plain=msg.getPlainBody();
        var preview=ftResearchDigestPreview_(plain,r[5]);
        var record=ftResearchEvidenceRow_(r,msg.getSubject(),preview,msg.getDate(),new Date());
        var targetRow=last+1;
        if(targetRow>evidence.getMaxRows())
          evidence.insertRowsAfter(evidence.getMaxRows(),Math.max(50,targetRow-evidence.getMaxRows()));
        var vals=ftQueueScreenEvidenceCells_(record,targetRow);
        // Never touch financial fields U:Z. Age/eligibility are existing
        // ledger formulas, and J=Unverified forces zero eligible weight.
        evidence.getRange(targetRow,1,1,20).setValues([vals]);
        SpreadsheetApp.flush();
        if(String(evidence.getRange(targetRow,15).getValue())!==msgId||
          String(evidence.getRange(targetRow,1).getValue())!==vals[0]||
          Number(evidence.getRange(targetRow,11).getValue())!==0)
          throw new Error("Evidence readback/zero-weight guard failed; queue remains pending");
        last=targetRow;seen[msgId]=true;
        queue.getRange(qr,7,1,4).setValues([["SCREENED",Number(r[7]||0)+1,
          new Date().toISOString(),"Evidence!A"+targetRow+
          "; "+record.theme+"; preview/headline unverified, zero weight; "+ftResearchQuestion_(record.theme)]]);
        saved++;
      } catch(e){failed++;console.log("Screen "+msgId+": "+String(e).slice(0,200));}
    }
    return {status:failed?"PARTIAL SCREEN":"SCREEN COMPLETE",scanned:scanned,
      saved:saved,duplicates:duplicates,excluded:excluded,failed:failed,
      pendingSeekingAlpha:Math.max(0,target.length-scanned)};
  } finally {lock.releaseLock();}
}
