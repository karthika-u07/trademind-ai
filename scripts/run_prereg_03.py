from __future__ import annotations
import hashlib, json, math, platform
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "prereg_03"
OUT = ROOT / "data" / "prereg_03_results"
EQUITY, SEED, NREP = 10_000.0, 20261012, 1000
DEV = pd.Timestamp("2006-01-01T00:00:00Z")
VAL = pd.Timestamp("2015-01-01T00:00:00Z")
END = pd.Timestamp("2022-10-01T00:00:00Z")
SPREAD = {"EURUSD":.00010,"GBPUSD":.00015,"USDJPY":.012,"AUDUSD":.00015,
          "USDCAD":.00020,"XAUUSD":.40,"XAGUSD":.03,"US500":1.0}
COMM = .000035
FINANCE = .01

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(rec, symbol):
    p = ROOT / rec["file"]
    if not p.is_file() or sha(p) != rec["sha256"]:
        raise RuntimeError(f"{symbol}: missing file or manifest SHA256 mismatch: {p}")
    d = pd.read_csv(p)
    required = {"timestamp","open","high","low","close"}
    if not required.issubset(d.columns): raise RuntimeError(f"{symbol}: missing OHLC/timestamp columns")
    d["timestamp"] = pd.to_datetime(d.timestamp, utc=True, errors="raise")
    if d.timestamp.duplicated().any() or not d.timestamp.is_monotonic_increasing:
        raise RuntimeError(f"{symbol}: duplicate/unordered timestamps")
    if (d.timestamp < pd.Timestamp("2005-01-01T00:00:00Z")).any() or (d.timestamp >= END).any():
        raise RuntimeError(f"{symbol}: timestamp outside permitted frozen sample")
    x = d[["open","high","low","close"]].to_numpy(float)
    if not np.isfinite(x).all() or (x <= 0).any(): raise RuntimeError(f"{symbol}: invalid OHLC")
    o,h,l,c=(d[k].to_numpy(float) for k in ("open","high","low","close"))
    if ((h < np.maximum.reduce([o,c,l])) | (l > np.minimum.reduce([o,c,h])) | (h < l)).any():
        raise RuntimeError(f"{symbol}: inconsistent OHLC")
    tr=np.maximum.reduce([h-l,np.r_[np.nan,np.abs(h[1:]-c[:-1])],np.r_[np.nan,np.abs(l[1:]-c[:-1])]])
    tr[0]=h[0]-l[0]
    atr=pd.Series(tr).rolling(20,min_periods=20).mean().to_numpy()
    sma=pd.Series(c).rolling(100,min_periods=100).mean().to_numpy()
    ts=pd.DatetimeIndex(d.timestamp)
    eligible=np.flatnonzero((np.arange(len(d))>=119)&(ts>=DEV)&(ts<END)&np.isfinite(atr)&(atr>0)&np.isfinite(sma))
    sig=np.zeros(len(d),dtype=np.int8); state=0
    for i in eligible:
        if c[i]>sma[i]: state=1
        elif c[i]<sma[i]: state=-1
        sig[i]=state
    established=sig[eligible]
    established=established[established!=0]
    flip=float(np.count_nonzero(np.diff(established)!=0)/(len(established)-1)) if len(established)>1 else float("nan")
    return {"ts":ts,"o":o,"c":c,"atr":atr,"eligible":eligible,"sig":sig,"flip":flip}

def period(t):
    if DEV <= t < VAL: return "development"
    if VAL <= t < END: return "validation"
    return None

def simulate(a,symbol,scenario,rng=None,mult=1,keep_ledger=False):
    ts,o,c,atr,eligible,sig=a["ts"],a["o"],a["c"],a["atr"],a["eligible"],a["sig"]
    ts_py = ts.to_pydatetime()
    ts_ns = ts.asi8
    dev_ns, val_ns, end_ns = DEV.value, VAL.value, END.value
    in_window = (ts_ns >= dev_ns) & (ts_ns < end_ns)
    in_dev = (ts_ns >= dev_ns) & (ts_ns < val_ns)
    elapsed_seconds = np.zeros(len(ts), dtype=np.float64)
    elapsed_seconds[1:] = np.diff(ts_ns) / 1e9
    date_keys = np.array([t.strftime("%Y-%m-%d") for t in ts_py], dtype=object)
    next_i = eligible + 1
    next_i = next_i[next_i < len(ts)]
    next_ns = ts.asi8[next_i]
    allowed = set(next_i[(next_ns >= DEV.value) & (next_ns < END.value)].tolist())
    daily={}; ledger=[]; pos=0; w=0.; entry=None; leg_side=0; leg_w=0.; gross_leg=0.; cost_leg=0.
    def add(i, v):
        if in_window[i]:
            key = date_keys[i]
            daily[key] = daily.get(key, 0.0) + float(v)
    def exec_cost(i, weight, price):
        v = EQUITY * abs(weight) * mult * (SPREAD[symbol] / price + COMM)
        add(i, -v)
        return v
    for i in range(1,len(ts)):
        if not in_window[i]:
            continue
        t = ts_py[i]
        if pos and in_window[i-1]:
            g=EQUITY*w*(o[i]/o[i-1]-1.)
            secs = elapsed_seconds[i]
            f=EQUITY*abs(w)*FINANCE*max(secs,0)/(365*86400)
            add(i, g-mult*f); gross_leg+=g; cost_leg+=mult*f
        # Last available open is liquidation-only.
        if i==len(ts)-1:
            if pos:
                cost_leg+=exec_cost(i,w,o[i])
                if keep_ledger: ledger.append(dict(symbol=symbol,scenario=f"{mult}x",side="BUY" if leg_side==1 else "SELL",entry_time=entry.isoformat(),exit_time=t.isoformat(),weight=leg_w,gross_pnl=gross_leg,costs=cost_leg,net_pnl=gross_leg-cost_leg,exit_reason="terminal_liquidation"))
                pos=0
            continue
        if i not in allowed: continue
        si=i-1
        if scenario=="strategy":
            desired=int(sig[si])
            if desired==0: continue
        elif pos==0:
            if not in_dev[i]:
                continue
            desired=int(rng.choice([-1,1]))
        else:
            p=a["flip"]
            desired=(-pos if np.isfinite(p) and rng.random()<p else pos)
        if pos and desired==pos: continue
        if pos:
            cost_leg+=exec_cost(i,w,o[i])
            if keep_ledger: ledger.append(dict(symbol=symbol,scenario=f"{mult}x",side="BUY" if leg_side==1 else "SELL",entry_time=entry.isoformat(),exit_time=t.isoformat(),weight=leg_w,gross_pnl=gross_leg,costs=cost_leg,net_pnl=gross_leg-cost_leg,exit_reason="signal_flip"))
            pos=0; gross_leg=0.; cost_leg=0.
        if not np.isfinite(atr[si]) or atr[si]<=0 or c[si]<=0: continue
        w=float(desired*.0025/(atr[si]/c[si])); pos=desired
        entry=t; leg_side=pos; leg_w=w
        cost_leg+=exec_cost(i,w,o[i])
    return daily,ledger

def series(daily, start, end):
    ix = pd.date_range(
        start=start,
        end=end - pd.Timedelta(days=1),
        freq="D",
        tz="UTC",
    )
    total = np.zeros(len(ix), dtype=np.float64)
    positions = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(ix)}
    for market_daily in daily.values():
        for day, value in market_daily.items():
            i = positions.get(day)
            if i is not None:
                total[i] += value
    return pd.Series(total, index=ix)

def sharpe(s):
    x=s.to_numpy(float)
    if len(x)<2: return None
    sd=float(np.std(x,ddof=1))
    return None if not math.isfinite(sd) or sd==0 else float(np.mean(x)/sd*math.sqrt(252))

def metrics(s):
    eq=EQUITY+s.cumsum(); peaks=np.maximum.accumulate(np.r_[EQUITY,eq.to_numpy()])[1:]
    return {"sharpe":sharpe(s),"net_pnl":float(s.sum()),"max_drawdown":float(np.max(peaks-eq.to_numpy())) if len(eq) else 0.,"days":len(s)}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((DATA/"manifest.json").read_text(encoding="utf-8-sig"))
    prereg=ROOT/"docs/research/preregistration_03.md"
    inputs={sym:load(rec,sym) for sym,rec in manifest["markets"].items()}
    result={"metadata":{"experiment":"Preregistration 03 daily time-series momentum","seed":SEED,"replicates":NREP,"numpy":np.__version__,"pandas":pd.__version__,"python":platform.python_version(),"manifest_sha256":sha(DATA/"manifest.json"),"preregistration_sha256":sha(prereg),"preregistration_commit":manifest.get("finalized_preregistration_commit"),"equity_reporting_scale":EQUITY,"gap_status":manifest.get("missing_bar_gap_status",{}).get("status","UNASSESSABLE"),"holdout_used":False,"boundary_convention":"Assign complete open-to-open interval to ending-open period; costs by execution timestamp."},"scenarios":{}}
    ledger_all=[]
    for mult in (1,2):
        md={}; trades={}; market_rows={}
        for sym,a in inputs.items():
            md[sym],trades[sym]=simulate(a,sym,"strategy",mult=mult,keep_ledger=True)
            ledger_all.extend(trades[sym])
            ss=series({sym:md[sym]},DEV,END)
            market_rows[sym]={"net_pnl":float(ss.sum()),"completed_legs":len(trades[sym]),"gross_pnl":sum(x["gross_pnl"] for x in trades[sym]),"costs":sum(x["costs"] for x in trades[sym]),"buy_legs":sum(x["side"]=="BUY" for x in trades[sym]),"sell_legs":sum(x["side"]=="SELL" for x in trades[sym])}
        pooled=series(md,DEV,END)
        periods={name:metrics(pooled.loc[(pooled.index>=a)&(pooled.index<b)]) for name,(a,b) in {"development":(DEV,VAL),"validation":(VAL,END),"pooled":(DEV,END)}.items()}
        legs=sum(map(len,trades.values()))
        pos_total=sum(max(v["net_pnl"],0) for v in market_rows.values())
        concentration=max((max(v["net_pnl"],0)/pos_total for v in market_rows.values()),default=1) if pos_total>0 else 1.
        # Reinitialize the same PCG64 stream for each cost scenario, pairing baseline paths.
        rng=np.random.Generator(np.random.PCG64(SEED)); base=[]; undef=[]
        for rep in range(NREP):
            if rep % 25 == 0:
                print(f"Random baseline replicate {rep}/{NREP}", flush=True)
            rd={}
            for sym,a in inputs.items():
                rd[sym],_=simulate(a,sym,"random",rng,mult=mult)
            val=sharpe(series(rd,DEV,END)); base.append(val)
            if val is None: undef.append(rep)
        if undef:
            baseline={"status":"BLOCKED","undefined_replicates":len(undef),"first_undefined_indices":undef[:20],"mean":None,"sample_std":None,"threshold":None}
        else:
            x=np.asarray(base,float); mean=float(x.mean()); sd=float(x.std(ddof=1))
            baseline={"status":"CALCULATED","undefined_replicates":0,"mean":mean,"sample_std":sd,"threshold":mean+2*sd}
        crit={"pooled_sharpe_positive":periods["pooled"]["sharpe"] is not None and periods["pooled"]["sharpe"]>0,"development_sharpe_positive":periods["development"]["sharpe"] is not None and periods["development"]["sharpe"]>0,"validation_sharpe_positive":periods["validation"]["sharpe"] is not None and periods["validation"]["sharpe"]>0,"at_least_300_legs":legs>=300,"concentration_le_40pct":pos_total>0 and concentration<=.40,"beats_baseline":baseline["status"]=="CALCULATED" and periods["pooled"]["sharpe"] is not None and periods["pooled"]["sharpe"]>=baseline["threshold"]}
        status="INCONCLUSIVE" if legs<300 else ("FAIL" if baseline["status"]!="CALCULATED" or not all(crit.values()) else "PASS_CANDIDATE")
        result["scenarios"][f"{mult}x"]={"strategy":{"periods":periods,"per_market":market_rows,"completed_legs":legs,"positive_contribution_share_max":concentration,"criteria":crit,"status":status},"random_baseline":baseline}
    result["overall_status"]="BLOCKED_REVIEW_REQUIRED"
    result["status_note"]="No final PASS: session-gap status is UNASSESSABLE and daily candle boundary/price basis remain unverified. Undefined baseline Sharpe blocks the baseline comparison; no values are dropped or imputed."
    (OUT/"experiment_03_report.json").write_text(json.dumps(result,indent=2,allow_nan=False),encoding="utf-8")
    pd.DataFrame(ledger_all).to_csv(OUT/"experiment_03_trade_ledger.csv",index=False)
    print("Report:",OUT/"experiment_03_report.json")
    print("Ledger:",OUT/"experiment_03_trade_ledger.csv")
    print("Overall:",result["overall_status"],"| gap:",result["metadata"]["gap_status"])
    for k,v in result["scenarios"].items():
        print(k,"strategy:",v["strategy"]["status"],"pooled Sharpe:",v["strategy"]["periods"]["pooled"]["sharpe"],"legs:",v["strategy"]["completed_legs"],"baseline:",v["random_baseline"]["status"],"undefined:",v["random_baseline"]["undefined_replicates"])
if __name__=="__main__": main()
