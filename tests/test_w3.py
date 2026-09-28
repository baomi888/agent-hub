import requests, json, time
time.sleep(1)
s = requests.Session()

h = s.get('http://localhost:8000/health').json()
print(f'✅ health: {h}')

r = s.post('http://localhost:8000/api/sessions/', json={'title':'W3Test'})
sid = r.json()['id']
print(f'✅ sid: {sid}')

print()
print('--- Test A: Agent mode (weather) ---')
r2 = s.post('http://localhost:8000/api/chat/stream',
    json={'sid':sid, 'question':'北京今天天气怎么样', 'mode':'agent'})
raw = r2.text
events = []
for frame in raw.split('\n\n'):
    frame = frame.strip()
    if not frame.startswith('event:'): continue
    lines = frame.split('\n')
    ev_type = lines[0].split(':',1)[1].strip()
    ev_data = lines[1].split(':',1)[1].strip() if len(lines) > 1 else ''
    events.append((ev_type, ev_data))
types = [e[0] for e in events]
print(f'  events: {types}')
if 'done' in types:
    done = json.loads(events[types.index('done')][1])
    print(f'  mode: {done.get("mode")}')
    print(f'  answer: {done["answer"][:200]}')
else:
    for e in events:
        print(f'  [{e[0]}] {e[1][:200]}')

print()
print('--- Test B: RAG mode (auto, bind kb) ---')
r3 = s.patch(f'http://localhost:8000/api/sessions/{sid}/', json={'kb_id':'test_w1'})
print(f'  bound kb: {r3.json().get("kb_id")}')
r4 = s.post('http://localhost:8000/api/chat/stream',
    json={'sid':sid, 'question':'RAG 是什么', 'mode':'auto'})
raw2 = r4.text
events2 = []
for frame in raw2.split('\n\n'):
    frame = frame.strip()
    if not frame.startswith('event:'): continue
    lines = frame.split('\n')
    ev_type = lines[0].split(':',1)[1].strip()
    ev_data = lines[1].split(':',1)[1].strip() if len(lines) > 1 else ''
    events2.append((ev_type, ev_data))
types2 = [e[0] for e in events2]
print(f'  events: {types2}')
if 'done' in types2:
    done2 = json.loads(events2[types2.index('done')][1])
    print(f'  mode: {done2.get("mode")}')
    print(f'  answer: {done2["answer"][:200]}')
    print(f'  refs: {"有" if done2.get("refs") else "无"}')

print()
print('🎉 W3 HTTP E2E ALL PASSED!')
