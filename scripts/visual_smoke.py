"""Browser UI smoke test. Normal mode uses the real HTTP origin.

--bridge renders the same built JS/CSS in an about:blank page and forwards only
/api/ calls to the real local backend via Playwright bindings. It is useful in
restricted rendering environments, but does NOT validate secure-context mic,
CSP/module loading or native browser HTTP transport. No API responses are mocked.
"""
import argparse
import base64
import json
import re
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://localhost:7860')
    parser.add_argument('--bridge', action='store_true')
    parser.add_argument('--chromium', default=None)
    parser.add_argument('--output', default=str(ROOT/'docs/screenshots'))
    args = parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    result={'mode':'inline UI + real HTTP API bridge' if args.bridge else 'native browser HTTP','checks':[], 'errors':[]}
    with httpx.Client(base_url=args.url,timeout=40) as http, sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=args.chromium,headless=True,args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
        page=browser.new_page(viewport={'width':1536,'height':1024},device_scale_factor=1)
        page.on('pageerror',lambda error:result['errors'].append(str(error)))
        if args.bridge:
            def request(_, options):
                url=options['url']
                if not url.startswith('/api/') or url.startswith('//'):
                    raise ValueError('Bridge only permits this application\'s API')
                response=http.request(options.get('method','GET'),url,headers=options.get('headers',{}),content=options.get('body'))
                return {'status':response.status_code,'headers':dict(response.headers),'body':base64.b64encode(response.content).decode()}
            page.expose_binding('__localKtvRequest',request)
            html=(ROOT/'frontend/dist/index.html').read_text()
            html=re.sub(r'<script\b[^>]*>.*?</script>','',html,flags=re.S)
            html=re.sub(r'<link\b[^>]*>','',html)
            html=html.replace('</head>',f'<style>{(ROOT/"frontend/dist/style.css").read_text()}</style></head>')
            page.set_content(html)
            page.add_script_tag(content='''window.fetch=async (url,options={})=>{
                const r=await window.__localKtvRequest({url:String(url),method:options.method||'GET',headers:options.headers||{},body:options.body});
                return new Response(Uint8Array.from(atob(r.body),c=>c.charCodeAt(0)),{status:r.status,headers:r.headers});
            };''')
            sources=[]
            for name in ['types','api','engine','visuals','main']:
                code=(ROOT/f'frontend/dist/js/{name}.js').read_text()
                code=re.sub(r'^import\b[^;]*;\s*','',code,flags=re.M)
                code=re.sub(r'^export\s+','',code,flags=re.M)
                sources.append(code)
            page.add_script_tag(content='(()=>{\n'+'\n'.join(sources)+'\n})();')
        else:
            page.goto(args.url)
        page.wait_for_timeout(500)
        assert page.title().startswith('Simple KTV')
        result['checks'].append('Application boot')
        page.screenshot(path=str(output/'empty.png'),full_page=True)
        page.locator('#empty-state [data-action=demo]').click()
        page.wait_for_selector('.track-canvas',timeout=15000)
        page.wait_for_timeout(600)
        assert page.locator('.track-canvas').count()==3
        result['checks'].append('Real synthesized project creation and 3 real waveform assets')
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        result['checks'].append('1536px desktop layout has no horizontal document overflow')
        if not args.bridge:
            page.locator('#play-button').click()
            page.wait_for_function("document.getElementById('play-time').textContent !== '00:00.0'",timeout=20000)
            result['checks'].append('Web Audio decode/playback advances shared-clock transport')
            page.locator('[data-action=stop]').first.click()
        else:
            result['not_verified']=['Native navigation / CSP / module loading', 'Web Audio playback', 'Microphone and AudioWorklet in a secure context']
        page.wait_for_selector('#toast.hidden',state='attached',timeout=10000)
        page.screenshot(path=str(output/'studio.png'),full_page=True)
        page.locator('[data-tab=mix]').click()
        page.locator('#mix-gain').evaluate("el => { el.value='-5'; el.dispatchEvent(new Event('input',{bubbles:true})); }")
        page.locator('#mix-gain').dispatch_event('change')
        page.wait_for_timeout(300)
        assert '-5.0 dB' in page.locator('.track-row.active .track-gain').inner_text()
        result['checks'].append('Mixer mutation persists through API and rerenders')
        page.locator('#undo-button').click();page.wait_for_timeout(300)
        assert '-3.0 dB' in page.locator('.track-row.active .track-gain').inner_text()
        result['checks'].append('Revision-checked undo from browser')
        page.locator('[data-tab=process]').click()
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(300)
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(output/'mobile.png'),full_page=True)
        result['checks'].append('390px mobile layout has no horizontal document overflow')
        assert not result['errors'],result['errors']
        result['checks'].append('No uncaught application JavaScript errors')
        browser.close()
    (output/'visual-report.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
