// Faithful JS port of freight_audit's engine (match.py + normalize.py + models money rules).
// Pure logic, no DOM. Used to run the real audit on the repo's sample loads in-browser,
// and (via run_script) to produce findings.json. Kept deliberately close to the Python.

export const VOCAB = {
  linehaul:  ["linehaul", "line haul", "freight charge", "base rate", "transportation", "flat rate"],
  fuel:      ["fuel", "fsc", "fuel surcharge"],
  detention: ["detention", "det ", "det.", "driver wait", "wait time", "waiting", "demurrage"],
  layover:   ["layover", "lay over", "overnight"],
  lumper:    ["lumper", "unloading", "loading fee", "load/unload", "handling"],
  liftgate:  ["liftgate", "lift gate", "lift-gate"],
  reweigh:   ["reweigh", "re-weigh", "reweighing", "scale"],
  tonu:      ["tonu", "truck order not used", "dry run", "dead head", "deadhead"],
  stopoff:   ["stop off", "stop-off", "extra stop", "multi-stop", "additional stop"],
  residential: ["residential", "resi "]
};

export function toCents(value) {
  if (value === null || value === undefined) return 0;
  if (typeof value === "boolean") return 0;
  if (typeof value === "number") return Math.round(value * 100);
  let s = String(value).trim().replace(/\$/g, "").replace(/,/g, "").replace(/\s/g, "");
  if (s === "") return 0;
  const neg = s.startsWith("(") && s.endsWith(")");
  s = s.replace(/[()]/g, "");
  const f = parseFloat(s);
  if (isNaN(f)) return 0;
  const cents = Math.round(f * 100);
  return neg ? -cents : cents;
}

export function centsToStr(c) {
  const sign = c < 0 ? "-" : "";
  c = Math.abs(c);
  return sign + "$" + Math.trunc(c / 100).toLocaleString("en-US") + "." + String(c % 100).padStart(2, "0");
}

export function normalizeCategory(description) {
  const d = String(description || "").toLowerCase();
  for (const cat in VOCAB) {
    for (const kw of VOCAB[cat]) {
      if (d.indexOf(kw) !== -1) return cat;
    }
  }
  return "other";
}

export function isSameLoad(a, b) {
  const clean = (x) => {
    let s = String(x).toUpperCase().replace(/[^A-Z0-9]/g, "").replace(/^0+/, "");
    return s || "0";
  };
  const ca = clean(a), cb = clean(b);
  if (ca === cb) return true;
  if (ca.endsWith(cb) || cb.endsWith(ca) || ca.indexOf(cb) !== -1 || cb.indexOf(ca) !== -1) return true;
  return false;
}

function hoursOnSite(pod) {
  if (!pod || !pod.arrival_time || !pod.departure_time) return null;
  const p = (t) => { const m = String(t).replace(" ", "T"); const d = new Date(m); return isNaN(d) ? null : d; };
  const a = p(pod.arrival_time), b = p(pod.departure_time);
  if (!a || !b) return null;
  return (b - a) / 3600000;
}

const CFG = {
  total_tolerance_cents: 100,
  default_detention_rate_cents: 7500,
  detention_round_hours: 0.25,
  pod_required_categories: ["detention", "layover"],
  max_detention_hours: 8.0,
  implausible_time_on_site_hours: 14.0
};

function F(type, severity, message, money) { return { type, severity, message, money_impact_cents: money || 0 }; }

function detentionRate(rc) {
  if (rc) {
    for (const li of rc.line_items) {
      if (li.category === "detention" && li.rate_cents) return li.rate_cents;
    }
    const cap = rc.approved_accessorials["detention"];
    if (typeof cap === "number" && cap > 0) return cap;
  }
  return CFG.default_detention_rate_cents;
}

function podTimeUsable(tos) {
  return tos !== null && tos >= 0 && tos <= CFG.implausible_time_on_site_hours;
}

// bundle: the raw sample-load JSON object
export function matchLoad(bundle) {
  const rcRaw = bundle.rate_confirmation || null;
  const invRaw = bundle.invoice || null;
  const podRaw = bundle.pod || null;

  const mkItems = (doc) => (doc && doc.line_items ? doc.line_items.map(li => ({
    description: li.description, amount_cents: toCents(li.amount), category: normalizeCategory(li.description),
    quantity: li.quantity != null ? li.quantity : null, rate_cents: li.rate != null ? toCents(li.rate) : null
  })) : []);

  const rc = rcRaw ? {
    load_id: rcRaw.load_id, broker_name: rcRaw.broker_name, carrier_name: rcRaw.carrier_name,
    origin: rcRaw.origin, destination: rcRaw.destination,
    agreed_total_cents: toCents(rcRaw.agreed_total), free_time_hours: rcRaw.free_time_hours != null ? rcRaw.free_time_hours : 2.0,
    line_items: mkItems(rcRaw),
    approved_accessorials: Object.fromEntries(Object.entries(rcRaw.approved_accessorials || {}).map(([k, v]) => [k, v == null ? null : toCents(v)]))
  } : null;
  const inv = invRaw ? {
    invoice_number: invRaw.invoice_number, load_id: invRaw.load_id, carrier_name: invRaw.carrier_name,
    billed_total_cents: toCents(invRaw.billed_total), invoice_date: invRaw.invoice_date, line_items: mkItems(invRaw)
  } : null;
  const pod = podRaw ? {
    load_id: podRaw.load_id, delivered: !!podRaw.delivered, arrival_time: podRaw.arrival_time,
    departure_time: podRaw.departure_time, signed_by: podRaw.signed_by, time_on_site_hours: hoursOnSite(podRaw)
  } : null;

  const load_id = (rc && rc.load_id) || (inv && inv.load_id) || "UNKNOWN";
  const findings = [];

  if (inv === null) {
    findings.push(F("missing_doc", "block", "No carrier invoice present; nothing to audit."));
    return finalize(load_id, findings, rc, inv, pod);
  }
  if (rc === null) {
    findings.push(F("missing_doc", "block", "No rate confirmation present; cannot validate charges against agreement."));
  }

  // integrity: load ids
  const ids = [];
  if (rc) ids.push(["rate con", rc.load_id]);
  if (inv) ids.push(["invoice", inv.load_id]);
  if (pod) ids.push(["POD", pod.load_id]);
  let mismatched = false;
  for (let i = 0; i < ids.length && !mismatched; i++) {
    for (let j = i + 1; j < ids.length; j++) {
      if (!isSameLoad(ids[i][1], ids[j][1])) {
        findings.push(F("load_id_mismatch", "block",
          ids[i][0] + " references load '" + ids[i][1] + "' but " + ids[j][0] + " references '" + ids[j][1] + "' -- documents may be mis-matched. Do not pay until resolved."));
        mismatched = true; break;
      }
    }
  }

  // integrity: pod data
  if (pod && pod.time_on_site_hours !== null) {
    const tos = pod.time_on_site_hours;
    if (tos < 0) findings.push(F("bad_pod_data", "block", "POD departure time is before arrival time (" + tos.toFixed(2) + "h) -- bad data; detention cannot be computed. Verify the POD."));
    else if (tos > CFG.implausible_time_on_site_hours) findings.push(F("bad_pod_data", "warn", "POD shows " + tos.toFixed(1) + "h on site -- implausibly long (likely a date or OCR error, or a layover). Verify before billing."));
  }

  const tol = CFG.total_tolerance_cents;

  if (rc) {
    // total
    const diff = inv.billed_total_cents - rc.agreed_total_cents;
    if (Math.abs(diff) > tol) {
      if (diff > 0) findings.push(F("total_mismatch", "warn", "Invoice total " + centsToStr(inv.billed_total_cents) + " exceeds agreed " + centsToStr(rc.agreed_total_cents) + " by " + centsToStr(diff) + ".", diff));
      else findings.push(F("total_mismatch", "info", "Invoice total " + centsToStr(inv.billed_total_cents) + " is " + centsToStr(-diff) + " BELOW agreed " + centsToStr(rc.agreed_total_cents) + ".", diff));
    }
    // line overcharges (linehaul, fuel)
    const sumCat = (items) => { const m = {}; items.forEach(li => m[li.category] = (m[li.category] || 0) + li.amount_cents); return m; };
    const rcc = sumCat(rc.line_items), ivc = sumCat(inv.line_items);
    ["linehaul", "fuel"].forEach(cat => {
      if (cat in rcc && cat in ivc) {
        const over = ivc[cat] - rcc[cat];
        if (over > tol) findings.push(F("line_overcharge", "warn", cat.charAt(0).toUpperCase() + cat.slice(1) + " billed " + centsToStr(ivc[cat]) + " vs agreed " + centsToStr(rcc[cat]) + " (+" + centsToStr(over) + ").", over));
      }
    });
    // unauthorized accessorials
    const rcCats = new Set(rc.line_items.map(li => li.category));
    const approved = new Set(Object.keys(rc.approved_accessorials));
    const baseline = new Set(["linehaul", "fuel", "other", ...rcCats, ...approved]);
    inv.line_items.forEach(li => {
      if (!baseline.has(li.category)) findings.push(F("unauthorized_accessorial", "warn", "Unauthorized accessorial '" + li.description + "' (" + li.category + ") billed " + centsToStr(li.amount_cents) + " -- not on rate con or pre-approved.", li.amount_cents));
    });
    // accessorial caps
    for (const cat in rc.approved_accessorials) {
      const cap = rc.approved_accessorials[cat];
      if (cap === null) continue;
      const billed = ivc[cat] || 0;
      if (billed > cap + tol) findings.push(F("accessorial_over_cap", "warn", cat.charAt(0).toUpperCase() + cat.slice(1) + " billed " + centsToStr(billed) + " exceeds approved cap " + centsToStr(cap) + " (+" + centsToStr(billed - cap) + ").", billed - cap));
    }
  }

  // duplicates
  const seen = {};
  inv.line_items.forEach(li => { const k = li.category + "|" + li.amount_cents; seen[k] = (seen[k] || 0) + 1; });
  Object.entries(seen).forEach(([k, count]) => {
    const amt = parseInt(k.split("|")[1], 10);
    if (count > 1 && amt > 0) {
      const cat = k.split("|")[0];
      findings.push(F("duplicate_line", "warn", "'" + cat + "' charge of " + centsToStr(amt) + " appears " + count + "x -- " + (count - 1) + " likely duplicate(s).", amt * (count - 1)));
    }
  });

  // detention
  const billedDet = inv.line_items.filter(li => li.category === "detention").reduce((s, li) => s + li.amount_cents, 0);
  const free = rc ? rc.free_time_hours : 2.0;
  const tos = (pod && podTimeUsable(pod.time_on_site_hours)) ? pod.time_on_site_hours : null;
  if (billedDet > 0) {
    if (tos === null) findings.push(F("detention_unsupported", "block", "Detention of " + centsToStr(billedDet) + " billed but POD has no usable arrival/departure timestamps to substantiate it -- deniable as-is.", 0));
    else if (Math.max(0, tos - free) <= 0) findings.push(F("detention_unsupported", "block", "Detention billed but POD shows only " + tos.toFixed(2) + "h on site (<= " + free.toFixed(1) + "h free) -- not supported.", 0));
  }
  if (billedDet === 0 && tos !== null) {
    const billable = Math.max(0, tos - free);
    const capped = Math.min(billable, CFG.max_detention_hours);
    if (billable >= CFG.detention_round_hours) {
      const rate = detentionRate(rc);
      const rounded = Math.round(capped / CFG.detention_round_hours) * CFG.detention_round_hours;
      const owed = Math.round(rounded * rate);
      const cappedNote = billable > CFG.max_detention_hours ? " (capped at " + CFG.max_detention_hours.toFixed(0) + "h; verify whether this was layover)" : "";
      findings.push(F("detention_underbilled", "warn", "POD shows " + tos.toFixed(2) + "h on site (" + rounded.toFixed(2) + "h past free time) but NO detention billed. Carrier is owed ~" + centsToStr(owed) + " @ " + centsToStr(rate) + "/hr -- recoverable revenue" + cappedNote + ".", -owed));
    }
  }

  // pod presence
  const needsPod = inv.line_items.some(li => CFG.pod_required_categories.indexOf(li.category) !== -1);
  if (pod === null && needsPod) findings.push(F("missing_pod", "block", "Invoice includes detention/layover but no POD attached to prove it.", 0));
  else if (pod === null) findings.push(F("missing_pod", "info", "No POD attached (not strictly required for these charges)."));
  else if (!pod.delivered) findings.push(F("missing_pod", "block", "POD present but marked NOT delivered -- do not pay."));

  if (findings.length === 0) findings.push(F("ok", "ok", "Invoice matches agreement and evidence; safe to auto-approve."));
  return finalize(load_id, findings, rc, inv, pod);
}

function finalize(load_id, findings, rc, inv, pod) {
  const order = ["ok", "info", "warn", "block"];
  let worst = "ok";
  findings.forEach(f => { if (order.indexOf(f.severity) > order.indexOf(worst)) worst = f.severity; });
  const net = findings.reduce((s, f) => s + f.money_impact_cents, 0);
  return {
    load_id, severity: worst, net_money_impact_cents: net,
    auto_approvable: worst === "ok" || worst === "info",
    findings, rate_con: rc, invoice: inv, pod
  };
}

export function runBatch(bundles) { return bundles.map(matchLoad); }
