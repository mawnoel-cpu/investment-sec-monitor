/**
 * FT GAME — metadata-only, resumable Gmail discovery.
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
  maxRows: 10000,
  deadline: "2026-11-27",
  headers: ["MESSAGE ID", "RECEIVED UTC", "PRIORITY", "TICKERS", "SENDER DOMAIN",
    "SUBJECT", "STATUS", "ATTEMPTS", "UPDATED UTC", "DISPOSITION / ISSUE"]
});

function ftGmailQueueCollect() {
  var result = ftQueueCollect_();
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
        if (lastNeeded > sheet.getMaxRows())
          sheet.insertRowsAfter(sheet.getMaxRows(), lastNeeded-sheet.getMaxRows());
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
  var portfolio=book.getSheetByName("Portfolio").getRange("A8:D19").getValues();
  for (var i=0; i<portfolio.length; i++) {
    var ticker=String(portfolio[i][0]||"").trim();
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

