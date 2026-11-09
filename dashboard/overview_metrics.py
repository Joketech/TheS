import config
"""Overview projections of already authorised group execution records.

No I/O: retain the existing reader's scope and identity deduplication. Counts use
all supplied executions; display windows must never become the denominator.
"""
from datetime import datetime,timezone
AGENTS=('mira','sage','seline','surge','spectra')
def period_projection(rows,asof):
 unique={r['id']:r for r in rows if isinstance(r,dict) and isinstance(r.get('id'),str)}
 dated=[];undated=0
 for row in unique.values():
  try:
   date=datetime.fromisoformat(row['submitted_at'].replace('Z','+00:00'))
   if date.tzinfo is None or date.timestamp()<=0:raise ValueError()
   stamp=date.timestamp()
  except (KeyError,AttributeError,TypeError,ValueError,OverflowError):undated+=1;continue
  if stamp<=asof:dated.append((stamp,row))
 recent=[row for _,row in sorted(dated,key=lambda x:(x[0],x[1]['id']),reverse=True)[:20]]
 period=[row for stamp,row in dated if stamp>=asof-86400]
 return {'runs':len(period),'completed':sum(r.get('status')=='completed' for r in period),'recent':recent,'undated':undated}
def combine_periods(profiles,asof):
 available=all(profiles.get(k,{}).get('status')=='available' and isinstance(profiles[k].get('overview'),dict) for k in AGENTS)
 counts={k:profiles.get(k,{}).get('overview',{}).get('runs') for k in AGENTS}
 available=available and all(type(n) is int and n>=0 for n in counts.values())
 total=sum(counts.values()) if available else None
 completed=sum(profiles[k]['overview']['completed'] for k in AGENTS) if available else None
 maximum=max(counts.values()) if available and total else None
 leaders=[k for k,n in counts.items() if n==maximum] if maximum is not None else []
 unique={r['id']:r for p in profiles.values() for r in p.get('overview',{}).get('recent',[])}
 recent=sorted(unique.values(),key=lambda r:(r['submitted_at'],r['id']),reverse=True)[:20]
 return {'period':'Last 24 hours · by submission time','start':datetime.fromtimestamp(asof-86400,timezone.utc).isoformat(),'end':datetime.fromtimestamp(asof,timezone.utc).isoformat(),'coverage':'Retained Mission Control group executions only; private chats and standalone CLI excluded','status':'available' if available else 'incomplete','total':total,'completed':completed,'counts':counts,'leaders':leaders,'focus_share':round(maximum/total*100,1) if total and maximum is not None else None,'recent':recent,'undated_excluded':sum(p.get('overview',{}).get('undated',0) for p in profiles.values())}
