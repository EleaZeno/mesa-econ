import sys
sys.path.insert(0, r'D:\qclaw\resources\openclaw\config\skills\browser-cdp\scripts')
import time

from cdp_client import CDPClient
from browser_actions import BrowserActions
from page_snapshot import PageSnapshot

try:
    client = CDPClient("http://127.0.0.1:54380")
    client.connect()
    print("CDP connected")

    tabs = client.list_tabs()
    print(f"Found {len(tabs)} tabs")

    # Find or create gradio tab
    gradio_tab = None
    for t in tabs:
        if '7860' in t.get('url', ''):
            gradio_tab = t
            break

    if gradio_tab:
        client.attach(gradio_tab['id'])
        print(f"Attached to existing tab: {gradio_tab['url']}")
    else:
        gradio_tab = client.create_tab("http://127.0.0.1:7860")
        client.attach(gradio_tab['id'])
        print(f"Created new tab")

    actions = BrowserActions(client, PageSnapshot(client))
    actions.wait_for_load(timeout=15)
    time.sleep(3)

    out = r"C:\Users\Kanyun\.qclaw\workspace\mesa-econ\screenshot.png"
    actions.screenshot(out, full_page=False)
    print(f"Screenshot saved to {out}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
