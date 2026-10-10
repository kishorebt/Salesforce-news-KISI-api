import json, os, urllib.request
from pathlib import Path
from publish_news import ROOT, STATE, CANDIDATE, buffer_post
if not CANDIDATE.exists():
    print('No candidate; skipping.')
    raise SystemExit(0)
data=json.loads(CANDIDATE.read_text())
item=data['item']
state=json.loads(STATE.read_text()) if STATE.exists() else {'published_urls': []}
if item['url'] in state.get('published_urls', []):
    print('Already published; skipping.')
    raise SystemExit(0)
commit=urllib.request.urlopen('https://api.github.com/repos/kishorebt/Salesforce-news-KISI-api/commits/main', timeout=20)
sha=json.loads(commit.read())['sha']
image_url=f"https://raw.githubusercontent.com/kishorebt/Salesforce-news-KISI-api/{sha}/{data['image_path']}"
post=buffer_post(data['caption'], image_url)
state.setdefault('published_urls', []).append(item['url'])
state['published_urls']=state['published_urls'][-500:]
state['last_success']={'source_url':item['url'],'buffer_post_id':post['id'],'image_url':image_url}
STATE.parent.mkdir(parents=True,exist_ok=True)
STATE.write_text(json.dumps(state,indent=2)+'\n')
print('BUFFER_POST_CREATED '+post['id'])
print('SOURCE_URL '+item['url'])
