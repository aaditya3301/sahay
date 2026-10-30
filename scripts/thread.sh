#!/usr/bin/env bash
# usage: scripts/thread.sh <channel> <phone> [tenant_file]
TOKEN=$(cat scripts/.token); T=$(cat ${3:-scripts/.ll})
for i in $(seq 1 90); do
  J=$(curl -s "localhost:8080/v1/simulator/thread?channel=$1&phone=$(python -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$2")" -H "Authorization: Bearer $TOKEN" -H "X-Tenant-Id: $T")
  echo "$J" | python -c "import sys,json;sys.exit(0 if not json.load(sys.stdin)['typing'] else 1)" && break
  sleep 2
done
echo "$J" | PYTHONIOENCODING=utf-8 python -c "
import sys,json;d=json.load(sys.stdin)
print('typing',d['typing'])
for m in d['messages']: print('>>' if m['direction']=='in' else '<<', m['author_type'], '|', (m['text'] or '')[:700])"
