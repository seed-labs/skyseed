#!/usr/bin/env python3
"""Copy the fixed demo bundle and exercise its local browser interactions."""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, unquote
from check_documentation import _HTMLReferences
from _webdriver import _available_port, _request, _execute, _wait_for_driver, _stop_driver, _capture_screenshot
from check_commit_prep_tools_browser import _physical_world, _planner, _compiler, _performance, _navigate, _click_first_canvas_point


def run(bundle, evidence, driver):
    evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='portable-demo-',dir=evidence.parent) as temporary:
        root=Path(temporary)/'demo';shutil.copytree(bundle,root)
        # Every browser link/asset is contained in the copied bundle. Textual provenance
        # may name original records; browser loading must never depend on those paths.
        for page in root.rglob('*.html'):
            refs=_HTMLReferences();refs.feed(page.read_text())
            for raw in refs.links:
                parts=urlsplit(raw)
                if parts.scheme or not parts.path:continue
                target=(page.parent/unquote(parts.path)).resolve()
                assert target.is_relative_to(root) and target.exists(),(page,raw)
        port=_available_port();base=f'http://127.0.0.1:{port}';session=None
        with (evidence/'webdriver.log').open('w') as log:
            process=subprocess.Popen((driver,'--port',str(port)),stdout=log,stderr=subprocess.STDOUT)
            try:
                _wait_for_driver(base,30)
                created=_request(base+'/session',method='POST',value={'capabilities':{'alwaysMatch':{'browserName':'firefox','moz:firefoxOptions':{'args':['-headless']}}}})
                session=created['sessionId'];_request(f'{base}/session/{session}/window/rect',method='POST',value={'width':1600,'height':1200})
                def js(code):return _execute(base,session,code)
                def shot(name):
                    js('window.scrollTo(0,0);');_capture_screenshot(base,session,evidence,name+'.png')
                def healthy():
                    value=js('return {errors:window.SKYSEED_PAGE_ERRORS||[],resources:performance.getEntriesByType("resource").map(r=>r.name)};')
                    assert not value['errors'],value
                    for url in value['resources']:
                        parsed=urlsplit(url)
                        assert parsed.scheme in {'file','data'},url
                        if parsed.scheme=='file':assert Path(unquote(parsed.path)).is_relative_to(root),url
                    return value
                result={'browser':created['capabilities']['browserVersion'],'copy_outside_source':True}
                _navigate(base,session,root/'index.html');shot('demo-index')
                assert js('return document.querySelectorAll(".links a").length')>=8
                js("""document.querySelector('a[href="s5/index.html"]').click();""");time.sleep(.2)
                assert js('return location.pathname.endsWith("s5/index.html")')
                result['physical-world']=_physical_world(base,session,root/'s5/physical-world/index.html');healthy();shot('physical-world')
                result['planner']=_planner(base,session,root/'s5/planner/index.html');healthy();shot('planner')
                result['compiler']=_compiler(base,session,root/'s5/bpf-compiler/index.html');healthy();shot('compiler')
                result['performance']=_performance(base,session,root/'s5/performance/index.html');healthy();shot('performance')
                _navigate(base,session,root/'s5/zone/index.html')
                js('document.querySelector("#zone-map [data-zone]").dispatchEvent(new MouseEvent("click",{bubbles:true}));')
                result['zone']=js('return {selected:document.getElementById("selected-zone").dataset.selectedZone,errors:window.SKYSEED_PAGE_ERRORS||[]};')
                assert result['zone']['selected'];healthy();shot('zone')
                _navigate(base,session,root/'s5/runner/index.html')
                result['runner']=js('document.querySelector("#graphical-timeline [data-detail]").click();document.querySelector("[data-local-event]")?.click();const t=document.querySelector("#timeline details");if(t)t.open=true;return {events:document.querySelectorAll("#timeline > *").length,activations:document.querySelectorAll("#activation-table tbody tr").length,outcome:document.getElementById("outcome-badge").textContent};')
                assert result['runner']['activations']>0;healthy();shot('runner')
                _navigate(base,session,root/'s5/traffic/index.html')
                result['traffic_initial']=js('return {flows:document.getElementById("traffic-flow").options.length,max:document.getElementById("traffic-seek").max};')
                assert result['traffic_initial']['flows']>1
                js('const f=document.getElementById("traffic-flow");f.selectedIndex=1;f.dispatchEvent(new Event("change",{bubbles:true}));const t=document.getElementById("traffic-seek");t.value=Number(t.max)/2;t.dispatchEvent(new Event("input",{bubbles:true}));')
                time.sleep(.5)
                result['traffic']=js('return {flow:document.getElementById("traffic-flow").value,seek:document.getElementById("traffic-seek").value,label:document.getElementById("traffic-play-time").textContent,status:document.getElementById("traffic-selection-status").textContent};')
                assert float(result['traffic']['seek'])>0;healthy();shot('traffic')
                _navigate(base,session,root/'s3/planner/index.html')
                js('document.querySelector("[data-stage=route]").click();document.getElementById("ospf-areas").click();const t=document.getElementById("planner-time");t.value=t.max;t.dispatchEvent(new Event("input",{bubbles:true}));')
                time.sleep(.5)
                _click_first_canvas_point(base,session,'planned-map')
                result['s3_entity']=js('return document.getElementById("planned-map").dataset.selectedId;')
                # The ordinary entity/pair UI loads independently recorded directional routes.
                js('document.getElementById("pair-global").click();const buttons=[...document.querySelectorAll("#pair-list [data-pair-key]")];const chosen=buttons.find(b=>b.textContent.includes("GS → Zone: reachable"))||buttons[0];chosen?.click();')
                time.sleep(.5)
                result['s3']=js('return {areas:document.querySelectorAll("[data-ospf-area]").length,frame:document.getElementById("planner-time").value,selected:document.getElementById("planned-map").dataset.selectedId,routeEdges:Number(document.getElementById("planned-map").dataset.routeEdgeCount),route:document.getElementById("pair-outcome")?.textContent,focus:document.getElementById("focus-detail")?.textContent,stage:document.getElementById("stage-title")?.textContent,special:document.getElementById("special")?.textContent.slice(0,2000)};')
                assert result['s3']['areas']>1 and result['s3_entity'] and 'Selected pair' in (result['s3']['route'] or '') and result['s3']['routeEdges']>0 and 'OSPF selected-path' in result['s3']['route'],result['s3']
                healthy();shot('s3-area')
                js('document.getElementById("planned-map").scrollIntoView({block:"center"});')
                _capture_screenshot(base,session,evidence,'s3-area-map.png')
                # A leaf's common demo link returns to the copied portal.
                js('document.querySelector("[data-demo-nav] a").click();');time.sleep(.2)
                assert js('return document.title')=='SkySEED · Interactive demonstrations'
                result['status']='PASS';(evidence/'interactions.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
                print(json.dumps({'status':'PASS','browser':result['browser'],'copied_bundle':True,'views':8}))
            finally:
                if session:_request(f'{base}/session/{session}',method='DELETE')
                _stop_driver(process,port)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundle',type=Path);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--geckodriver',default='/snap/bin/geckodriver')
    a=p.parse_args();run(a.bundle.resolve(),a.evidence.resolve(),a.geckodriver)


if __name__=='__main__':main()
