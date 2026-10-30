#!/usr/bin/env bash
# usage: scripts/ticket.sh [tenant_file]  -> prints the most recently updated live ticket trace
TOKEN=$(cat scripts/.token); T=$(cat ${1:-scripts/.ll})
TID=$(curl -s "localhost:8080/v1/tickets?status=all" -H "Authorization: Bearer $TOKEN" -H "X-Tenant-Id: $T" | python -c "import sys,json;r=sorted(json.load(sys.stdin),key=lambda x:x['updated_at'],reverse=True);print(r[0]['id'])")
curl -s "localhost:8080/v1/tickets/$TID" -H "Authorization: Bearer $TOKEN" -H "X-Tenant-Id: $T" | PYTHONIOENCODING=utf-8 python -c "
import sys,json;d=json.load(sys.stdin);t=d['ticket']
print('ticket',t['number'],t['status'],t['intent'],t['urgency'],'frustration',t['frustration_score'],'prio',t['priority_score'],t['resolution_type'],'inj',t['injection_suspected'],'sens',t['sensitive'])
for r in d['runs']:
  print('run latency ms', r['latency_ms'], r['status'], r['error'])
  for s in r['steps']: print('  ',s['seq'],s['type'],s['name'],s['latency_ms'],'ms', json.dumps(s['output'],ensure_ascii=False)[:180])
for a in d['actions']: print('action',a['type'],a['status'],a['decision'],'|',a['decision_reason'])
for a in d['approvals']: print('approval',a['kind'],a['status'],a['summary'])
for m in d['messages']:
  for at in (m['attachments'] or []): print('attachment', at['kind'], json.dumps(at['analysis'])[:260] if at['analysis'] else None)
"
